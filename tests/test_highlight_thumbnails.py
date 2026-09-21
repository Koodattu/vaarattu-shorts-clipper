import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import highlight_thumbnails as thumbs, highlights
from vaarattu_shorts.storage import atomic_json, digest
from vaarattu_shorts.web import create_app

JPEG = b"\xff\xd8\xffTEST\xff\xd9"


@pytest.fixture
def finished(settings, monkeypatch):
    store = highlights.store_for(settings)
    run_id = store.admit({"manifest": {"title": "Recording", "sources": []}}, "thumbnail-test")
    folder = highlights.revision_folder(settings, run_id, 1)
    atomic_json(folder / "plan.json", {"retained": [{"asset": "s0", "start_us": 0, "end_us": 20000000}]})
    (folder / "draft.mp4").write_bytes(b"unchanged video")
    store.update(run_id, state="completed", result={"revision": 1, "has_draft": True, "review": "approved",
                 "plan_sha256": digest(folder / "plan.json")})
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-not-for-output")
    def capture(args, *a, **kw):
        args[-1].write_bytes(JPEG)
    monkeypatch.setattr(thumbs, "run_tool", capture)
    return store, run_id, folder


def test_frame_uses_output_time_and_final_when_available(settings, finished, monkeypatch):
    store, run_id, folder = finished
    (folder / "final.mp4").write_bytes(b"final video")
    run = store.get(run_id)
    store.update(run_id, result={**run["result"], "has_final": True})
    calls = []
    def capture(args, *a, **kw):
        calls.append(args)
        args[-1].write_bytes(JPEG)
    monkeypatch.setattr(thumbs, "run_tool", capture)
    frame = thumbs.capture(settings, store, run_id, 1, 12.5)
    args = calls[0]
    assert args[args.index("-ss")+1] == "12.500000"
    assert args[args.index("-i")+1] == folder / "final.mp4"
    assert frame["quality"] == "final"
    assert thumbs.image_path(settings, store, run_id, 1, frame["id"]).read_bytes() == JPEG


@pytest.mark.parametrize("seconds", [-1, 20, float("nan"), float("inf")])
def test_invalid_frames_never_run_ffmpeg(settings, finished, monkeypatch, seconds):
    store, run_id, _ = finished
    monkeypatch.setattr(thumbs, "run_tool", lambda *a, **kw: pytest.fail("Invalid seek"))
    with pytest.raises(ValueError, match="inside"):
        thumbs.capture(settings, store, run_id, 1, seconds)


def test_generator_keeps_candidates_and_is_idempotent(settings, finished, monkeypatch):
    store, run_id, folder = finished
    frame = thumbs.capture(settings, store, run_id, 1, 1)
    before = store.get(run_id)
    calls = []
    def image(key, image, prompt, quality):
        calls.append((image, prompt, quality))
        assert image.read_bytes() == JPEG
        return JPEG, {"total_tokens": 42}
    monkeypatch.setattr(thumbs, "request_image", image)
    first = thumbs.generate(settings, store, run_id, 1, frame["id"], "a"*32, "No text", "high")
    assert first["status"] == "completed" and first["usage"]["total_tokens"] == 42
    assert thumbs.generate(settings, store, run_id, 1, frame["id"], "a"*32) == first
    second = thumbs.generate(settings, store, run_id, 1, frame["id"], "b"*32)
    assert len(calls) == 2
    assert "No text" in calls[0][1] and calls[0][2] == "high"
    result = thumbs.public(settings, store.get(run_id))
    assert len(result["items"]) == 3
    assert "test-secret" not in json.dumps(result)
    for item in (first, second):
        assert thumbs.image_path(settings, store, run_id, 1, item["id"]).read_bytes() == JPEG
    assert store.get(run_id) == before
    assert (folder / "draft.mp4").read_bytes() == b"unchanged video"


def test_stale_frame_and_changed_revision_never_call_image_api(settings, finished, monkeypatch):
    store, run_id, folder = finished
    frame = thumbs.capture(settings, store, run_id, 1, 0)
    monkeypatch.setattr(thumbs, "request_image", lambda *a: pytest.fail("Stale frame"))
    atomic_json(folder / "plan.json", {"retained": []})
    with pytest.raises(ValueError, match="changed"):
        thumbs.generate(settings, store, run_id, 1, frame["id"], "a"*32)
    store.update(run_id, result={"revision": 2})
    with pytest.raises(ValueError, match="Refresh"):
        thumbs.generate(settings, store, run_id, 1, frame["id"], "b"*32)


def test_timeout_is_saved_without_paid_retry(settings, finished, monkeypatch):
    store, run_id, _ = finished
    frame = thumbs.capture(settings, store, run_id, 1, 0)
    calls = []
    def fail(*a):
        calls.append(1)
        raise ValueError("Check usage before trying again.")
    monkeypatch.setattr(thumbs, "request_image", fail)
    first = thumbs.generate(settings, store, run_id, 1, frame["id"], "a"*32)
    assert first["status"] == "failed"
    assert thumbs.generate(settings, store, run_id, 1, frame["id"], "a"*32) == first
    assert calls == [1]


def test_missing_key_stops_before_generation(settings, finished, monkeypatch):
    store, run_id, _ = finished
    frame = thumbs.capture(settings, store, run_id, 1, 0)
    monkeypatch.delenv("OPENAI_API_KEY")
    assert not thumbs.public(settings, store.get(run_id))["configured"]
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        thumbs.generate(settings, store, run_id, 1, frame["id"], "a"*32)


def test_edits_api_uploads_reference_and_returns_jpeg(tmp_path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(JPEG)
    calls = []
    def handle(request):
        calls.append(request)
        body = request.read()
        assert request.url == "https://api.openai.com/v1/images/edits"
        assert request.headers["authorization"] == "Bearer test-key"
        for value in (JPEG, b'gpt-image-2.5-sunburst', b'1536x864', b'name="image"', b'name="output_format"', b'jpeg'):
            assert value in body
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(JPEG).decode()}], "usage": {"total_tokens": 10}})
    original = httpx.Client
    monkeypatch.setattr(thumbs.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    assert thumbs.request_image("test-key", frame, "Prompt", "medium") == (JPEG, {"total_tokens": 10})
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 403, 429, 502])
def test_service_errors_are_sanitized_and_not_retried(tmp_path, monkeypatch, status):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(JPEG)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="upstream-private-details")
    original = httpx.Client
    monkeypatch.setattr(thumbs.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(ValueError) as error:
        thumbs.request_image("secret", frame, "Prompt", "medium")
    assert "upstream-private-details" not in str(error.value)
    assert len(calls) == 1


def test_thumbnail_routes_local_token_validation_and_download(settings, finished, monkeypatch):
    store, run_id, _ = finished
    monkeypatch.setattr(thumbs, "request_image", lambda *a: (JPEG, None))
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        url = f"/api/highlights/{run_id}/thumbnail"
        assert client.post(url+"/frame", json={"revision": 1, "seconds": 0}).status_code == 403
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        frame = client.post(url+"/frame", headers=headers, json={"revision": 1, "seconds": 0})
        assert frame.status_code == 200, frame.text
        body = {"revision": 1, "frame_id": frame.json()["id"], "request_id": "a"*32}
        generated = client.post(url+"/generate", headers=headers, json=body)
        assert generated.status_code == 200, generated.text
        assert generated.json()["status"] == "completed"
        download = client.get(url+"/"+"a"*32+"?revision=1&download=true")
        assert download.content == JPEG
        assert "attachment" in download.headers["content-disposition"]
        assert client.post(url+"/generate", headers=headers, json={**body, "frame_id": "../bad"}).status_code == 422
        assert client.post(url+"/generate", headers=headers, json={**body, "revision": 2, "request_id": "b"*32}).status_code == 400
        assert client.get("/api/highlights").json()[0]["thumbnails"]["configured"] is True
