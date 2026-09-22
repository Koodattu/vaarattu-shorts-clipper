import copy
import json

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import highlight_episode as episode, highlight_selection as selection, highlights
from vaarattu_shorts.storage import atomic_json, digest
from vaarattu_shorts.web import create_app


@pytest.fixture
def saved(settings):
    store = highlights.store_for(settings)
    config = {"manifest": {"title": "Recording", "sources": [{"asset": "s0", "duration": 200}]},
              "provider": "codex", "video_encoder": "h264_nvenc", "model_manifests": {}}
    run_id = store.admit(config, "score-preview")
    transcript = {"duration_us": 200000000, "words": [
        {"id": f"w{i}", "start_us": i*10000000+1000000, "end_us": i*10000000+3000000,
         "text": f"Thought {i}.", "probability": 1} for i in range(10)], "timing_issues": []}
    atomic_json(highlights.run_folder(settings, run_id) / "s0" / "asr" / "transcript.json", transcript)
    items = episode.units({"s0": transcript})
    pool = []
    for ident, first, last in [("a", "u0", "u1"), ("b", "u2", "u3"), ("c", "u4", "u5"), ("d", "u6", "u7")]:
        pool.append({"beat": {"id": ident, "first": first, "last": last, "value": 3,
                             "reason": "Story", "continuation": ""},
                     "edit": {"spans": [{"first": first, "last": last}], "gaps": [], "value": 3, "reason": "Story"}})
    pool[-1]["excluded_as_duplicate_of"] = "a"
    ratings = [{"id": ident, "score": score, "reason": "Value"} for ident, score in [("a", 90), ("b", 75), ("c", 65), ("d", 95)]]
    picked, decisions = episode.assemble(pool, ratings, items, 75)
    retained = episode.timeline(picked, items)
    plan = {"version": episode.VERSION, "scene_pool": pool, "rankings": ratings, "sequences": picked,
            "retained": retained, "selection": decisions, "final_score_floor": 75,
            "duration": episode.seconds(retained), "issues": [], "metrics": {}, "warnings": []}
    folder = highlights.revision_folder(settings, run_id, 1)
    atomic_json(folder / "plan.json", plan)
    (folder / "draft.mp4").write_bytes(b"original draft")
    media = folder / "source.mp4"
    media.write_bytes(b"verified source")
    atomic_json(folder / "media.json", [{"asset": "s0", "path": str(media), "start_us": 0, "end_us": 200000000,
                "mapping": {"origin_us": 0, "section_duration": 200}, "sha256": digest(media)}])
    result = {"revision": 1, "has_draft": True, "has_final": False, "review": "approved",
              "plan_sha256": digest(folder / "plan.json"), "media_sha256": digest(folder / "media.json")}
    store.update(run_id, state="completed", result={**result, "history": [result]})
    return store, run_id, folder, plan, items


def test_preview_restores_omitted_scenes_but_not_duplicates_and_matches_frame_rounding(settings, saved):
    store, run_id, folder, plan, items = saved
    before = store.get(run_id)
    preview = selection.preview(settings, store, run_id, 1)
    assert len(preview["previews"]) == 101
    assert preview["previews"][60]["scenes"] == 3
    assert preview["previews"][60]["added"] == 1
    assert preview["previews"][75]["scenes"] == 2
    assert preview["previews"][80]["scenes"] == 1
    assert preview["previews"][100]["scenes"] == 0
    for floor in [0, 60, 75, 80, 100]:
        selected, _ = episode.assemble(plan["scene_pool"], plan["rankings"], items, floor)
        spans = episode.timeline(selected, items)
        expected = sum(max(1, round((s["end_us"]-s["start_us"])/1e6*30)) for s in spans)/30
        assert preview["previews"][floor]["duration"] == expected
    assert store.get(run_id) == before
    assert (folder / "draft.mp4").read_bytes() == b"original draft"
    assert not (folder.parent / "2").exists()


def test_saved_cuts_remain_authoritative_across_compiler_changes(settings, saved, monkeypatch):
    store, run_id, folder, plan, items = saved
    plan["retained"][0]["end_us"] += 12345
    atomic_json(folder / "plan.json", plan)
    original = copy.deepcopy(plan["retained"])
    _, _, _, items, spans = selection.snapshot(settings, store, run_id, 1)
    selected, _, retained = selection.select(plan, items, spans, 80)
    assert len(selected) == 1
    assert retained[0]["end_us"] == original[0]["end_us"]
    assert all(s["sequence"] == "a" for s in retained)


def test_preview_and_render_share_selection_preserve_history_and_use_no_ai(settings, saved, monkeypatch):
    store, run_id, folder, plan, _ = saved
    before = {name: digest(folder / name) for name in ("plan.json", "draft.mp4", "media.json")}
    preview = selection.preview(settings, store, run_id, 1)
    queued = selection.queue(settings, store, run_id, 1, 65, preview["plan_hash"])
    assert queued["revision"] == 2 and store.get(run_id)["state"] == "queued"
    next_folder = folder.parent / "2"
    next_plan = json.loads((next_folder / "plan.json").read_text("utf-8"))
    assert len(next_plan["sequences"]) == preview["previews"][65]["scenes"]
    def forbidden(*a, **kw):
        pytest.fail("Score-only render must not call AI or fetch cached footage")
    monkeypatch.setattr(highlights, "Evaluator", forbidden)
    monkeypatch.setattr(highlights.transcribe, "transcribe", forbidden)
    monkeypatch.setattr(highlights.highlight_copy, "automatic", forbidden)
    monkeypatch.setattr(highlights.sources, "acquire", forbidden)
    def render(settings, plan, media, output, *a):
        output.write_bytes(b"new draft")
        assert plan == next_plan
        assert media
        return {"duration": preview["previews"][65]["duration"], "sha256": digest(output)}
    monkeypatch.setattr(highlights, "render_video", render)
    store.claim()
    result = highlights.Highlights(settings, store, run_id).execute()
    assert result["duration"] == preview["previews"][65]["duration"]
    assert result["review"] == "unreviewed" and len(result["history"]) == 2
    assert result["final_score_floor"] == 65
    assert before == {name: digest(folder / name) for name in before}
    assert not (next_folder / "publishing-copy.json").exists()


@pytest.mark.parametrize("fault", ["stale_hash", "stale_revision", "empty", "unchanged", "running"])
def test_queue_rejects_invalid_or_stale_preview(settings, saved, fault):
    store, run_id, folder, plan, _ = saved
    plan_hash = digest(folder / "plan.json")
    revision, floor = 1, 80
    if fault == "stale_hash":
        plan_hash = "0"*64
    elif fault == "stale_revision":
        revision = 2
    elif fault == "empty":
        floor = 100
    elif fault == "unchanged":
        floor = 75
    else:
        store.update(run_id, state="running")
    with pytest.raises(ValueError):
        selection.queue(settings, store, run_id, revision, floor, plan_hash)
    assert not (folder.parent / "2" / "plan.json").exists()


def test_selection_api_preview_is_readonly_and_render_requires_local_token(settings, saved):
    store, run_id, _, _, _ = saved
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        url = f"/api/highlights/{run_id}/selection"
        preview = client.get(url+"?revision=1")
        assert preview.status_code == 200
        body = {"revision": 1, "score_floor": 80, "plan_hash": preview.json()["plan_hash"]}
        assert client.post(url, json=body).status_code == 403
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        assert client.post(url, headers=headers, json={**body, "score_floor": 101}).status_code == 422
        response = client.post(url, headers=headers, json=body)
        assert response.status_code == 202, response.text
        assert response.json()["revision"] == 2
        assert client.post(url, headers=headers, json=body).status_code == 400


def test_selection_fails_if_saved_render_plan_changes(settings, saved, monkeypatch):
    store, run_id, folder, _, _ = saved
    selection.queue(settings, store, run_id, 1, 80, digest(folder / "plan.json"))
    atomic_json(folder.parent / "2" / "plan.json", {"retained": []})
    monkeypatch.setattr(highlights, "render_video", lambda *a: pytest.fail("Changed edit"))
    store.claim()
    with pytest.raises(ValueError, match="selected edit changed"):
        highlights.Highlights(settings, store, run_id).execute()


@pytest.mark.parametrize("changed_audio", [False, True])
def test_newly_included_footage_uses_saved_audio_without_transcribing(settings, saved, monkeypatch, changed_audio):
    store, run_id, folder, _, _ = saved
    audio = folder / "audio.opus"
    audio.write_bytes(b"original audio")
    atomic_json(highlights.run_folder(settings, run_id) / "audio-s0.checkpoint.json",
                {"result": {"path": str(audio)}, "artifacts": [{"path": str(audio), "sha256": digest(audio)}]})
    (folder / "source.mp4").unlink()
    if changed_audio:
        audio.write_bytes(b"changed audio")
    selection.queue(settings, store, run_id, 1, 65, digest(folder / "plan.json"))
    calls = []
    def acquire(settings, source, where, check, window):
        calls.append(window)
        where.mkdir(parents=True, exist_ok=True)
        path = where / "source.mp4"
        path.write_bytes(b"new verified footage")
        return path
    def align(settings, audio_path, path, start, *a, **kw):
        assert audio_path == audio
        return {"origin_us": round(start*1e6), "section_duration": 200}
    def render(settings, plan, media, output, *a):
        output.write_bytes(b"new draft")
        return {"duration": 15, "sha256": digest(output)}
    monkeypatch.setattr(highlights.sources, "acquire", acquire)
    monkeypatch.setattr(highlights.sources, "acquire_aligned", lambda settings, source, where, check, window, audio_path, spans: (acquire(settings, source, where, check, window), align(settings, audio_path, None, window[0])))
    monkeypatch.setattr(highlights, "render_video", render)
    monkeypatch.setattr(highlights.transcribe, "transcribe", lambda *a, **kw: pytest.fail("No transcription"))
    store.claim()
    if changed_audio:
        with pytest.raises(ValueError, match="Original audio is missing or changed"):
            highlights.Highlights(settings, store, run_id).execute()
        assert calls == []
    else:
        highlights.Highlights(settings, store, run_id).execute()
        assert calls
        assert store.get(run_id)["state"] == "completed"
