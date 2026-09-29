import copy
import json

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import clip_trim, pipeline, tighten
from vaarattu_shorts.contracts import Word
from vaarattu_shorts.storage import atomic_json
from vaarattu_shorts.web import create_app


@pytest.fixture(params=[False, True])
def trim_fixture(settings, store, request):
    run = store.admit(dict(video="abc_def-ghI", final_transcription=True, correct_captions=True), "trim")
    store.update(run, state="completed")
    words = [Word(id=f"w{i}", start_us=i*1000000+100000, end_us=i*1000000+800000,
                  text=f"word{i}").model_dump() for i in range(40)]
    transcript = dict(duration_us=40000000, words=words)
    folder = settings.work / "runs" / run
    atomic_json(folder / "asr/transcript.json", transcript)
    source = folder / "section.mkv"
    source.write_bytes(b"source preview")
    preview = settings.ready / "clip" / "1"
    preview.mkdir(parents=True)
    (preview / "short.mp4").write_bytes(b"preview")
    body = dict(title="Clip", start_us=10000000, end_us=30000000, words=copy.deepcopy(words[10:30]),
                status="ready", folder=str(preview), layout={"name": "Saved layout"}, section=str(source),
                mapping=dict(origin_us=0, section_duration=40), trim_silence=True, video_encoder="h264_nvenc")
    if request.param:
        words = [{**w, "id": "f-"+w["id"]} for w in words[5:35]]
        body["caption_transcript"] = dict(profile="large-v3", start_us=5000000, end_us=35000000, words=words)
        body["words"] = copy.deepcopy(words[5:25])
    body["words"][2]["text"] = "Manual correction"
    body["caption_locked_word_ids"] = [body["words"][2]["id"]]
    cut = tighten.Cut(start_word_id=body["words"][8]["id"], end_word_id=body["words"][10]["id"], reason="Detour")
    body["speech_cuts"] = tighten.resolve(body, [cut])
    body["speech_cut_history"] = [[]]
    store.save_clip("clip", run, 1, body)
    store.review_clip("clip", 1, "ready_to_post")
    return run, body, transcript


@pytest.mark.parametrize("start,end", [(6000000, 34000000), (12400000, 27400000), (22000000, 28000000)])
def test_trim_preserves_captions_cuts_and_layout_and_queues_only_render(settings, store, trim_fixture, start, end):
    run, original, transcript = trim_fixture
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        response = client.get("/api/clips/clip/trim")
        assert response.status_code == 200
        available = response.json()
        assert available["clip"]["revision"] == 1
        assert available["max_us"] == (35000000 if original.get("caption_transcript") else 40000000)
        assert next(w for w in available["words"] if w["id"] == original["words"][2]["id"])["text"] == "Manual correction"
        response = client.post("/api/clips/clip/trim", headers=headers,
                               json=dict(expected_revision=1, start_us=start, end_us=end))
        assert response.status_code == 202, response.text
        saved = store.clip("clip")
        assert saved["revision"] == 2 and saved["review_status"] == "approved"
        body = saved["body"]
        assert body["start_us"] == (12100000 if start == 12400000 else start)
        assert body["end_us"] == (27800000 if end == 27400000 else end)
        for key in ("layout", "mapping", "section", "trim_silence", "video_encoder", "caption_locked_word_ids"):
            assert body[key] == original[key]
        if start < 20000000:
            assert body["speech_cuts"] == original["speech_cuts"]
            assert next(w for w in body["words"] if w["id"] == original["words"][2]["id"])["text"] == "Manual correction"
        else:
            assert body["speech_cuts"] == []
        assert body["speech_cut_history"] == []
        assert body["previous_revision"] == 1
        assert body["reviewed"] is True and body["caption_correction_pass"] == "manual-trim"
        if original.get("caption_transcript"):
            assert body["caption_transcript"] == original["caption_transcript"]
            assert all(w["id"].startswith("f-") for w in body["words"])
        assert tighten.intervals(body) == [[c["start_us"], c["end_us"]] for c in body["speech_cuts"]]
        assert body["output_duration_us"] >= 3000000
        assert store.get(run)["stage"] == "rerender"
        assert (settings.ready / "clip/1/short.mp4").read_bytes() == b"preview"
        assert json.loads((settings.work / "runs" / run / "asr/transcript.json").read_text()) == transcript
        stale = client.post("/api/clips/clip/trim", headers=headers,
                            json=dict(expected_revision=1, start_us=start, end_us=end))
        assert stale.status_code == 400 and store.clip("clip")["revision"] == 2


@pytest.mark.parametrize("start,end", [(0, 41000000), (25000000, 20000000), (10000000, 11000000),
                                       (10000000, 30000000), (18400000, 28000000)])
def test_invalid_trim_does_not_mutate_clip(settings, store, trim_fixture, start, end):
    _, original, _ = trim_fixture
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        response = client.post("/api/clips/clip/trim", headers=headers,
                               json=dict(expected_revision=1, start_us=start, end_us=end))
        assert response.status_code == 400, response.text
        assert store.clip("clip")["body"] == original
        assert store.clip("clip")["revision"] == 1


def test_missing_source_is_actionable(settings, store, trim_fixture):
    run, _, _ = trim_fixture
    (settings.work / "runs" / run / "section.mkv").unlink()
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        result = client.get("/api/clips/clip/trim")
        assert result.status_code == 400 and "source preview is unavailable" in result.json()["detail"]
        assert store.clip("clip")["revision"] == 1


def test_manual_trim_skips_asr_and_llm_during_rerender(settings, store, trim_fixture, monkeypatch):
    run, original, transcript = trim_fixture
    revised = clip_trim.apply(original, clip_trim.context(original, transcript), 6000000, 34000000)
    store.queue_edit("clip", 1, revised)
    audio = settings.work / "runs" / run / "audio.opus"
    audio.write_bytes(b"audio")
    atomic_json(audio.parent / "audio.checkpoint.json", {"result": {"path": str(audio), "duration": 40}})
    def forbidden(*args, **kwargs):
        raise AssertionError("Manual trim must not call ASR or the LLM")
    monkeypatch.setattr(pipeline.transcribe, "refine_clips", forbidden)
    monkeypatch.setattr(pipeline, "Evaluator", forbidden)
    instance = pipeline.Pipeline(settings, store, run)
    delivered = []
    monkeypatch.setattr(instance, "deliver", lambda clip, *args: delivered.append(clip))
    monkeypatch.setattr(instance, "finish", lambda *args: None)
    instance.rerender()
    assert len(delivered) == 1 and delivered[0]["body"]["start_us"] == 6000000


def test_overlapping_words_snap_to_outer_edge():
    words = [dict(start_us=10, end_us=20), dict(start_us=15, end_us=25)]
    assert clip_trim.snap(18, "start", words) == 10
    assert clip_trim.snap(18, "end", words) == 25
