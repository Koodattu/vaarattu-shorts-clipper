import json

import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import r2
from vaarattu_shorts.web import create_app
from test_posting_and_cleanup import rendered


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch):
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)


@pytest.fixture
def cloud(monkeypatch):
    objects, requests = {}, []
    real_client = httpx.Client

    def respond(request):
        requests.append(request)
        if request.url.host == "publishing.vaarattu.tv":
            data = objects.get(request.url.path, b"")
            return httpx.Response(200 if data else 404,
                                  headers={"content-type": "video/mp4", "content-length": str(len(data))})
        assert request.url.host == f"{r2.ACCOUNT}.r2.cloudflarestorage.com"
        assert "SignedHeaders=" in request.headers["authorization"]
        if request.method == "PUT":
            assert request.headers["x-amz-content-sha256"] == r2.hashlib.sha256(request.content).hexdigest()
            objects[request.url.path.removeprefix("/" + r2.BUCKET)] = request.content
            return httpx.Response(200)
        return httpx.Response(200, text='<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                              '<IsTruncated>false</IsTruncated></ListBucketResult>')

    monkeypatch.setattr(r2.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    return objects, requests


def test_credentials_saved_in_env_without_changing_other_settings_or_returning_secrets(settings, cloud):
    path = settings.root / ".env"
    path.write_text("# Existing settings\nOTHER_KEY='keep-this'\nR2_ACCESS_KEY_ID=old\n")
    result = r2.connect(settings, "a" * 32, "b" * 64)
    assert result == {"connected": True, "used_bytes": 0}
    raw = path.read_text()
    assert "# Existing settings\nOTHER_KEY='keep-this'\n" in raw
    assert "b" * 64 in raw
    assert raw.count("R2_ACCESS_KEY_ID=") == 1
    assert r2.credentials(settings) == {"access_key": "a" * 32, "secret_key": "b" * 64}
    assert "b" * 64 not in json.dumps(r2.status(settings))


def test_upload_checks_revision_and_preserves_identical_video(settings, store, cloud):
    r2.connect(settings, "a" * 32, "b" * 64)
    run = store.admit({"video": "source"}, "r2")
    store.update(run, state="completed")
    rendered(settings, store, run, "clip", 0)
    with pytest.raises(ValueError, match="exact clip revision"):
        r2.upload(settings, store, "clip", 2)
    record = r2.upload(settings, store, "clip", 1)
    assert record["url"].startswith(r2.PUBLIC_URL + "/clips/")
    assert list(cloud[0].values()) == [b"final-clip"]
    assert r2.upload(settings, store, "clip", 1) == record
    assert sum(req.method == "PUT" for req in cloud[1]) == 1
    store.review_clip("clip", 1, "approved")
    with pytest.raises(ValueError, match="Ready for posting"):
        r2.upload(settings, store, "clip", 1)


def test_modified_file_and_storage_limit_block_upload(settings, store, cloud, monkeypatch):
    r2.connect(settings, "a" * 32, "b" * 64)
    run = store.admit({"video": "source"}, "r2")
    store.update(run, state="completed")
    rendered(settings, store, run, "clip", 0)
    monkeypatch.setattr(r2.Client, "used_bytes", lambda self: r2.STORAGE_LIMIT)
    with pytest.raises(ValueError, match="8 GB"):
        r2.upload(settings, store, "clip", 1)
    final = settings.ready / "clip" / "1" / "short.mp4"
    final.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        r2.upload(settings, store, "clip", 1)
    assert not cloud[0]


def test_public_link_must_serve_the_actual_video_size(cloud):
    with pytest.raises(ValueError, match="public video link"):
        r2.verify_public({"url": r2.PUBLIC_URL + "/missing.mp4", "bytes": 20})


def test_invalid_key_never_echoed_in_api_response(settings):
    app = create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    token = client.get("/api/status").json()["token"]
    secret = "do-not-echo-this-secret"
    response = client.post("/api/publishing/storage/connect", headers={"x-local-token": token},
                           json={"access_key": secret, "secret_key": secret})
    assert response.status_code == 400
    assert secret not in response.text
    assert client.post("/api/publishing/storage/connect", json={}).status_code == 403
