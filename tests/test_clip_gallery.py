from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts.storage import Store
from vaarattu_shorts.storage import atomic_json
from vaarattu_shorts.web import create_app


def rendered_clip(settings, store, run, clip_id, status="ready"):
    folder = settings.work / "previews" / clip_id
    folder.mkdir(parents=True)
    (folder / "short.mp4").write_bytes(b"preview")
    body = dict(status=status, folder=str(folder), title="Tarina", start_us=0, end_us=15000000,
                flags=[], words=[], layout={}, source_url="https://www.youtube.com/watch?v=abc_def-ghI")
    store.save_clip(clip_id, run, 1, body)
    return body


def test_gallery_includes_rendered_clips_from_all_runs_without_private_paths(settings, store):
    old_run = store.admit({}, "old-run")
    store.update(old_run, state="completed")
    rendered_clip(settings, store, old_run, "old")
    # The gallery must include runs older than the run history's 100-item limit.
    for i in range(101):
        run = store.admit({}, f"new-run-{i}")
        store.update(run, state="completed")
    rendered_clip(settings, store, run, "new")
    rendered_clip(settings, store, run, "held", "held")
    rendered_clip(settings, store, run, "pending", "pending")
    missing = rendered_clip(settings, store, run, "missing")
    (Path(missing["folder"]) / "short.mp4").unlink()
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        result = client.get("/api/clips")
    assert result.status_code == 200
    assert [c["id"] for c in result.json()] == ["held", "new", "old"]
    assert all(c["review_status"] == "unreviewed" for c in result.json())
    assert all("folder" not in c and "words" not in c and "layout" not in c for c in result.json())


def test_pacing_revision_preserves_original_preview_and_source_boundaries(settings, store):
    run = store.admit({}, "pacing")
    store.update(run, state="completed")
    original = rendered_clip(settings, store, run, "clip")
    prior = settings.ready / "clip" / "1"
    prior.mkdir(parents=True)
    (prior / "short.mp4").write_bytes(b"original pacing")
    atomic_json(prior / "metadata.json", original)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        result = client.post("/api/clips/clip/rerender", headers=headers, json={"expected_revision": 1, "trim_silence": True, "video_encoder": "h264_nvenc"})
        assert result.status_code == 202 and result.json()["revision"] == 2
        changed = store.clip("clip")["body"]
        assert changed["trim_silence"] and changed["video_encoder"] == "h264_nvenc"
        assert changed["previous_revision"] == 1
        assert all(changed[k] == original[k] for k in ("start_us", "end_us", "words", "layout"))
        assert client.get("/api/artifacts/clip/video?revision=1").content == b"original pacing"
        assert client.get("/api/artifacts/clip/video?revision=2").status_code == 404
        assert client.get("/api/artifacts/clip/video?revision=0").status_code == 404
        assert client.get("/api/artifacts/clip/video?revision=3").status_code == 404


def test_review_persists_can_be_changed_and_resets_on_new_revision(settings, store):
    run = store.admit({}, "review-run")
    store.update(run, state="completed")
    body = rendered_clip(settings, store, run, "clip")
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        url = "/api/clips/clip/review"
        assert client.post(url, json={"expected_revision": 1, "status": "approved"}).status_code == 403
        for status in ("approved", "not_approved", "unreviewed", "approved"):
            response = client.post(url, json={"expected_revision": 1, "status": status}, headers=headers)
            assert response.status_code == 200
            assert response.json()["review_status"] == status
            assert Store(store.path).clip("clip")["review_status"] == status
            assert client.get("/api/clips").json()[0]["review_status"] == status
        assert store.clip("clip")["body"] == body
        assert store.get(run)["state"] == "completed"
        # A worker save of the same render must not overwrite the human decision.
        store.save_clip("clip", run, 1, body)
        assert store.clip("clip")["review_status"] == "approved"
        assert client.post("/api/clips/clip/retry", json={"expected_revision": 1}, headers=headers).status_code == 202
        assert store.clip("clip")["revision"] == 2
        assert store.clip("clip")["review_status"] == "unreviewed"
        assert client.get("/api/clips").json() == []
        store.save_clip("clip", run, 2, body)
        assert client.post(url, json={"expected_revision": 1, "status": "approved"}, headers=headers).status_code == 400
        assert client.get("/api/clips").json()[0]["review_status"] == "unreviewed"
        assert client.post(url, json={"expected_revision": 2, "status": "approved"}, headers=headers).status_code == 200


def test_optional_review_reason_survives_decisions_and_stays_with_its_revision(settings, store):
    run = store.admit({}, "review-reason")
    body = rendered_clip(settings, store, run, "reason-clip")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        url = "/api/clips/reason-clip/review"
        payload = {"expected_revision": 1, "status": "not_approved", "note": "  Tarvitsee lisää kontekstia.  "}
        assert client.post(url, json=payload).status_code == 403
        result = client.post(url, json=payload, headers=headers)
        assert result.status_code == 200
        assert result.json()["review_note"] == "Tarvitsee lisää kontekstia."
        for status in ("approved", "unreviewed", "not_approved"):
            result = client.post(url, json={"expected_revision": 1, "status": status}, headers=headers)
            assert result.json()["review_note"] == "Tarvitsee lisää kontekstia."
        assert Store(store.path).clip("reason-clip")["review_note"] == "Tarvitsee lisää kontekstia."
        assert client.get("/api/clips").json()[0]["review_note"] == "Tarvitsee lisää kontekstia."
        assert store.clip("reason-clip")["body"] == body
        invalid = client.post(url, json={**payload, "note": "x" * 2001}, headers=headers)
        assert invalid.status_code == 422
        assert client.post(url, json={**payload, "note": ""}, headers=headers).json()["review_note"] == ""
        client.post(url, json=payload, headers=headers)
        store.save_clip("reason-clip", run, 2, body)
        assert client.get("/api/clips").json()[0]["review_note"] == ""
        assert client.post(url, json=payload, headers=headers).status_code == 400
        result = client.post(url, json={"expected_revision": 2, "status": "approved"}, headers=headers)
        assert result.json()["review_note"] == ""


def test_existing_review_table_gains_optional_note_without_losing_reviews(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE clip_reviews(clip_id TEXT PRIMARY KEY, revision INTEGER, status TEXT)")
        db.execute("INSERT INTO clip_reviews VALUES('saved',3,'not_approved')")
    Store(path)
    Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT clip_id,revision,status,note FROM clip_reviews").fetchone() == (
            "saved", 3, "not_approved", ""
        )


@pytest.mark.parametrize("status,exists", [("pending", True), ("ready", False)])
def test_review_rejects_unavailable_preview_and_invalid_requests(settings, store, status, exists):
    run = store.admit({}, "unavailable")
    body = rendered_clip(settings, store, run, "clip", status)
    if not exists:
        (Path(body["folder"]) / "short.mp4").unlink()
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        url = "/api/clips/clip/review"
        assert client.post(url, json={"expected_revision": 1, "status": "approved"}, headers=headers).status_code == 400
        assert client.post(url, json={"expected_revision": 1, "status": "bad"}, headers=headers).status_code == 422
        assert client.post(url, json={"expected_revision": 0, "status": "approved"}, headers=headers).status_code == 422
        assert client.post("/api/clips/missing/review", json={"expected_revision": 1, "status": "approved"}, headers=headers).status_code == 404
    assert store.clip("clip")["review_status"] == "unreviewed"


def test_queue_rerender_changes_only_layout_and_creates_unreviewed_revision(settings, store):
    run = store.admit({}, "layout-rerender")
    store.update(run, state="completed")
    original = rendered_clip(settings, store, run, "clip")
    original.update(reviewed=True, words=[{"id": "w1", "text": "Tarina"}], section="source.mkv")
    store.save_clip("clip", run, 1, original)
    store.review_clip("clip", 1, "approved")
    layout = {"name": "Alternate camera", "camera_height": 960}
    layout_id = store.add_layout(layout)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        payload = {"expected_revision": 1, "layout_id": layout_id}
        url = "/api/clips/clip/rerender"
        assert client.post(url, json=payload).status_code == 403
        assert client.post(url, json={"expected_revision": 1, "video_encoder": "unknown"}, headers=headers).status_code == 422
        assert client.post(url, json={**payload, "layout_id": "missing"}, headers=headers).status_code == 404
        assert client.post(url, json={**payload, "expected_revision": 2}, headers=headers).status_code == 400
        assert store.clip("clip")["body"] == original
        response = client.post(url, json=payload, headers=headers)
        assert response.status_code == 202
        assert response.json() == {"revision": 2, "run_id": run}
        assert client.post(url, json=payload, headers=headers).status_code == 400
        assert client.get("/api/clips").json() == []
    changed = store.clip("clip")
    assert changed["revision"] == 2 and changed["review_status"] == "unreviewed"
    assert changed["body"] == {**original, "layout": layout, "status": "pending", "flags": [], "folder": None, "previous_revision": 1}
    assert store.get(run)["state"] == "queued" and store.get(run)["stage"] == "rerender"
    store.add_layout({**layout, "camera_height": 600})
    assert store.clip("clip")["body"]["layout"] == layout
    # Once the worker finishes, the new revision returns to the gallery for review.
    store.save_clip("clip", run, 2, {**changed["body"], "status": "ready", "folder": original["folder"]})
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        clip = client.get("/api/clips").json()[0]
        assert clip["revision"] == 2 and clip["review_status"] == "unreviewed"


@pytest.mark.parametrize("state", ["queued", "running", "paused"])
def test_queue_rerender_does_not_change_clips_in_an_active_run(settings, store, state):
    run = store.admit({}, "busy-layout")
    store.update(run, state=state)
    original = rendered_clip(settings, store, run, "clip")
    layout_id = store.add_layout({"name": "Alternate camera"})
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        response = client.post("/api/clips/clip/rerender", json={"expected_revision": 1, "layout_id": layout_id}, headers=headers)
    assert response.status_code == 409
    assert store.clip("clip")["body"] == original and store.clip("clip")["revision"] == 1
