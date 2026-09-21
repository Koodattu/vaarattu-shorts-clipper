import copy
import json

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import highlight_edit, highlight_sources as sources, highlights
from vaarattu_shorts.storage import atomic_json, digest
from vaarattu_shorts.web import create_app


def manifest():
    return {"title": "Original", "duration": 400, "notes": [], "sources": [
        {"asset": "s0", "provider": "youtube", "id": "abc_def-ghI", "url": "https://www.youtube.com/watch?v=abc_def-ghI", "owner": "channel", "title": "First game", "duration": 200},
        {"asset": "s1", "provider": "twitch", "id": "123", "url": "https://www.twitch.tv/videos/123", "owner": "channel", "title": "Second day", "duration": 200}]}


def transcript():
    return {"duration_us": 200000000, "coverage": [[0, 200000000]], "timing_issues": [], "words": [
        {"id": "w0", "start_us": 0, "end_us": 1000000, "text": "EXCLUDED", "probability": 1},
        {"id": "w1", "start_us": 20500000, "end_us": 23000000, "text": "Selected content.", "probability": 1},
        {"id": "w2", "start_us": 39000000, "end_us": 42000000, "text": "CROSSES END", "probability": 1},
        {"id": "w3", "start_us": 150000000, "end_us": 160000000, "text": "OTHER GAME", "probability": 1}]}


def test_collection_resolves_independent_platforms_in_entered_order(settings, store, monkeypatch):
    data = manifest()
    lookup = {s["url"]: s for s in data["sources"]}
    monkeypatch.setattr(sources, "metadata", lambda settings, url, folder: dict(lookup[url]))
    urls = [s["url"] for s in reversed(data["sources"])]
    result = sources.resolve(settings, store, urls, settings.work / "test", collection=True)
    assert [s["provider"] for s in result["sources"]] == ["twitch", "youtube"]
    assert [s["asset"] for s in result["sources"]] == ["s0", "s1"]
    with pytest.raises(ValueError, match="Combine only"):
        sources.resolve(settings, store, urls, settings.work / "test")


def test_partial_youtube_part_does_not_require_siblings_when_matching_disabled(settings, store, monkeypatch):
    first = {**manifest()["sources"][0], "title": "Day (Part 1/2)"}
    monkeypatch.setattr(sources, "metadata", lambda *a: dict(first))
    monkeypatch.setattr(sources.catalog, "fetch_page", lambda *a, **kw: pytest.fail("No sibling search"))
    result = sources.resolve(settings, store, [first["url"]], settings.work / "test", collection=True, match_parts=False)
    assert len(result["sources"]) == 1


def test_project_ranges_are_immutable_ordered_and_named():
    original = manifest()
    before = copy.deepcopy(original)
    chosen = sources.select_manifest(original, [{"asset": "s1", "start_us": 20000000, "end_us": 80000000},
                                              {"asset": "s0", "start_us": 100000000, "end_us": 150000000}], "Diablo")
    assert original == before
    assert chosen["title"] == "Diablo" and chosen["duration"] == 110
    assert [s["asset"] for s in chosen["sources"]] == ["s1", "s0"]
    assert chosen["sources"][0]["duration"] == 200
    assert sources.select_manifest(original)["sources"] == before["sources"]


@pytest.mark.parametrize("ranges", [[], [{"asset": "s0", "start_us": 5, "end_us": 5}],
    [{"asset": "s9", "start_us": 0, "end_us": 10}], [{"asset": "s0", "start_us": 0, "end_us": 201000000}],
    [{"asset": "s0", "start_us": -1, "end_us": 10}], [{"asset": "s0", "start_us": 0, "end_us": 10}]*2])
def test_invalid_or_duplicate_ranges_are_rejected(ranges):
    with pytest.raises(ValueError):
        sources.select_manifest(manifest(), ranges)


def test_selected_transcript_excludes_outside_words_and_preserves_original_clock():
    source = {**manifest()["sources"][0], "selection_start_us": 20000000, "selection_end_us": 40000000}
    value = sources.selected_transcript(transcript(), source)
    assert [w["text"] for w in value["words"]] == ["Selected content."]
    assert value["words"][0]["start_us"] == 20500000
    assert value["coverage"] == [[20000000, 40000000]]
    raw = {"duration_us": 20000000, "coverage": [[0, 20000000]],
           "words": [{"id": "w0", "start_us": 0, "end_us": 1000000, "text": "Inside", "probability": 1}],
           "timing_issues": [{"start_us": 3000000, "end_us": 4000000}]}
    shifted = sources.selected_transcript(raw, source, 20000000)
    assert shifted["words"][0]["start_us"] == 20000000
    assert shifted["timing_issues"][0] == {"start_us": 23000000, "end_us": 24000000}
    units = highlight_edit.units({"s0": shifted})
    assert units[0]["start_us"] == 20000000
    assert all(u["end_us"] <= 40000000 for u in units)


def test_render_guard_rejects_footage_outside_selection():
    chosen = sources.select_manifest(manifest(), [{"asset": "s0", "start_us": 20000000, "end_us": 40000000}])
    plan = {"retained": [{"asset": "s0", "start_us": 20000000, "end_us": 40000000}]}
    sources.validate_selection(plan, chosen["sources"])
    plan["retained"][0]["start_us"] -= 1
    with pytest.raises(ValueError, match="outside"):
        sources.validate_selection(plan, chosen["sources"])


def test_reuse_checks_selected_coverage_and_reuses_full_audio_for_another_game(settings):
    store = highlights.store_for(settings)
    original = manifest()["sources"][1]
    run_id = store.admit({"manifest": {"sources": [original]}, "model_manifests": {"turbo": "same"}}, "cached")
    folder = highlights.run_folder(settings, run_id)
    folder.mkdir(parents=True)
    audio = folder / "audio.opus"
    audio.write_bytes(b"verified full audio")
    selected = sources.selected_transcript(transcript(), {**original, "selection_start_us": 10000000, "selection_end_us": 100000000})
    asr = folder / "transcript.json"
    atomic_json(asr, selected)
    atomic_json(folder / "audio-s1.checkpoint.json", {"result": {"path": str(audio), "duration": 200}, "artifacts": [{"path": str(audio), "sha256": digest(audio)}]})
    atomic_json(folder / "transcript-s1.checkpoint.json", {"result": selected, "artifacts": [{"path": str(asr), "sha256": digest(asr)}]})
    store.update(run_id, state="completed")
    wanted = {**original, "selection_start_us": 20000000, "selection_end_us": 40000000}
    assert sources.reusable_transcript(settings, wanted, {"turbo": "same"})["transcript"] == selected
    wanted.update(selection_start_us=150000000, selection_end_us=200000000)
    reused = sources.reusable_transcript(settings, wanted, {"turbo": "same"})
    assert reused["transcript"] is None and reused["audio"]["path"] == str(audio)
    audio.write_bytes(b"changed")
    assert sources.reusable_transcript(settings, wanted, {"turbo": "same"}) is None


def test_api_snapshots_selected_ranges_before_queue_and_restricts_embeds(settings, monkeypatch):
    monkeypatch.setattr("vaarattu_shorts.web.preflight", lambda *a: {})
    monkeypatch.setattr("vaarattu_shorts.web.codex_settings", lambda: {"model": "test"})
    app = create_app(settings)
    manifest_id = "a"*32
    atomic_json(settings.work / "highlights" / "manifests" / manifest_id / "manifest.json", manifest())
    with TestClient(app, base_url="http://localhost") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"], "Idempotency-Key": "source-project"}
        body = {"manifest_id": manifest_id, "project_title": "Diablo only", "segments": [{"asset": "s0", "start_us": 20000000, "end_us": 40000000}]}
        response = client.post("/api/highlights", headers=headers, json=body)
        assert response.status_code == 202, response.text
        stored = app.state.highlights_store.get(response.json()["id"])["config"]["manifest"]
        assert stored["title"] == "Diablo only" and stored["duration"] == 20
        assert len(stored["sources"]) == 1
        assert stored["sources"][0]["selection_start_us"] == 20000000
        body["segments"][0]["end_us"] = 300000000
        assert client.post("/api/highlights", headers=headers, json=body).status_code == 400
        csp = client.get("/").headers["content-security-policy"]
        assert "frame-src https://www.youtube.com https://player.twitch.tv;" in csp
        assert "script-src 'self';" in csp and "frame-ancestors 'none'" in csp


def test_partial_pipeline_transcribes_only_selection_and_edits_with_original_timestamps(settings, monkeypatch):
    store = highlights.store_for(settings)
    selected = sources.select_manifest(manifest(), [{"asset": "s0", "start_us": 20000000, "end_us": 40000000}])
    config = {"manifest": selected, "model_manifests": {}, "provider": "codex", "budget_usd": 0,
              "context_size": 32768, "discovery_reasoning": "low", "verification_reasoning": "low"}
    run_id = store.admit(config, "partial-pipeline")
    calls = []
    def acquire(settings, source, folder, check):
        folder.mkdir(parents=True)
        path = folder / "source.opus"
        path.write_bytes(b"full audio")
        return path
    def crop(args, settings, folder, *a, **kw):
        folder.mkdir(parents=True, exist_ok=True)
        assert args[args.index("-ss")+1] == "20.000000"
        assert args[args.index("-t")+1] == "20.000000"
        args[-1].write_bytes(b"selected audio")
    def transcribe(settings, path, duration, *a):
        assert path.name == "selected.wav" and duration == 20
        calls.append("transcribe")
        return {"duration_us": 20000000, "words": [{"id": "w0", "start_us": 500000, "end_us": 1000000, "text": "Selected game", "probability": 1}]}
    def plan(items, *a, **kw):
        assert len(items) == 1 and items[0]["speech_start_us"] == 20500000
        assert items[0]["asset"] == "s0"
        calls.append("plan")
        return {"retained": [], "sequences": []}
    monkeypatch.setattr(sources, "reusable_transcript", lambda *a: None)
    monkeypatch.setattr(sources, "acquire", acquire)
    monkeypatch.setattr(highlights.youtube, "probe", lambda *a: {"format": {"duration": "200"}})
    monkeypatch.setattr(highlights, "run_tool", crop)
    monkeypatch.setattr(highlights.transcribe, "transcribe", transcribe)
    monkeypatch.setattr(highlights, "Evaluator", lambda *a, **kw: object())
    monkeypatch.setattr(highlights.editing, "plan", plan)
    monkeypatch.setattr(highlights.Highlights, "render_draft", lambda self, result, plan, audio: plan)
    store.claim()
    highlights.Highlights(settings, store, run_id).execute()
    assert calls == ["transcribe", "plan"]
    path = highlights.run_folder(settings, run_id) / "s0" / "asr" / "transcript.json"
    assert json.loads(path.read_text("utf-8"))["coverage"] == [[20000000, 40000000]]
    assert not (path.parent / "selected.wav").exists()
