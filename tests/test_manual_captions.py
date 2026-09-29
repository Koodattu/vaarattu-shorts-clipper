import json

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts.storage import atomic_json
from vaarattu_shorts.web import create_app


@pytest.mark.parametrize("refined", [False, True])
def test_caption_only_edit_preserves_layout_cuts_and_canonical_timing(settings, store, refined):
    run = store.admit({}, "manual-captions")
    store.update(run, state="completed")
    words = [dict(id=f"w{i}", start_us=t, end_us=t + 500000, text=f"word{i}")
             for i, t in enumerate([500000, 3000000, 6000000])]
    transcript_path = settings.work / "runs" / run / "asr/transcript.json"
    atomic_json(transcript_path, {"duration_us": 8000000, "words": words})
    preview = settings.ready / "clip" / "1"
    preview.mkdir(parents=True)
    (preview / "short.mp4").write_bytes(b"saved preview")
    body = dict(start_us=0, end_us=8000000, title="Clip", words=words, status="ready", folder=str(preview),
                layout={"name": "Original unsaved layout"}, trim_silence=True, video_encoder="h264_nvenc",
                speech_cuts=[dict(start_us=2000000, end_us=4000000)],
                caption_check={"status": "complete"}, tighten_check={"status": "complete"})
    if refined:
        words = [{**w, "id": f"final-{w['id']}", "text": f"final-{w['text']}"} for w in words]
        body.update(words=words, caption_transcript={"profile": "large-v3", "start_us": 0, "end_us": 8000000, "words": words})
    store.save_clip("clip", run, 1, body)
    store.review_clip("clip", 1, "ready_to_post")
    edit = dict(expected_revision=1, start_us=0, end_us=8000000, title="Clip", reviewed=True,
                words=[{**w, "text": "Korjattu"} if i == 0 else w for i, w in enumerate(words)])
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        bad = [{**w, "start_us": 1} if i == 0 else w for i, w in enumerate(edit["words"])]
        response = client.post("/api/clips/clip/edit", headers=headers, json={**edit, "words": bad})
        assert response.status_code == 400
        assert store.clip("clip")["revision"] == 1
        response = client.post("/api/clips/clip/edit", headers=headers, json=edit)
        assert response.status_code == 202, response.text
        assert response.json() == {"revision": 2, "run_id": run}
        saved = store.clip("clip")
        assert saved["review_status"] == "approved"
        assert saved["body"]["words"] == [{**w, "probability": None} for w in edit["words"]]
        for key in ("layout", "start_us", "end_us", "speech_cuts", "trim_silence", "video_encoder"):
            assert saved["body"][key] == body[key]
        assert saved["body"]["caption_locked_word_ids"] == [words[0]["id"]]
        assert saved["body"]["caption_check"] is None
        assert saved["body"]["tighten_check"] is None
        assert saved["body"]["previous_revision"] == 1
        assert saved["body"]["caption_correction_pass"] == "manual-edit"
        assert store.get(run)["stage"] == "rerender"
        assert (preview / "short.mp4").read_bytes() == b"saved preview"
        assert json.loads(transcript_path.read_text())["words"][0]["text"] == "word0"
        if refined:
            assert saved["body"]["caption_transcript"] == body["caption_transcript"]
        stale = client.post("/api/clips/clip/edit", headers=headers, json=edit)
        assert stale.status_code == 400
        assert store.clip("clip")["revision"] == 2
