import json

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import highlight_edit as edit, highlight_sources as sources, highlights
from vaarattu_shorts.llm import ModelAnchorError
from vaarattu_shorts.storage import atomic_json
from vaarattu_shorts.web import create_app


def source(identity="abc_def-ghI", title="Recording", asset="s0"):
    return {"asset": asset, "provider": "youtube", "id": identity,
            "url": f"https://www.youtube.com/watch?v={identity}", "title": title,
            "owner": "channel", "duration": 120}


def transcript():
    return {"duration_us": 120000000, "words": [
        {"id": f"w{i}", "start_us": i*5000000+1000000, "end_us": i*5000000+3000000,
         "text": f"Passage {i}.", "probability": 1} for i in range(20)], "timing_issues": []}


def config():
    return {"manifest": {"title": "Recording", "sources": [source()]}, "model_manifests": {},
            "provider": "codex", "budget_usd": 0, "context_size": 32768,
            "target_minutes": 5, "discovery_reasoning": "low", "verification_reasoning": "medium",
            "video_encoder": "h264_nvenc"}


@pytest.mark.parametrize("value,expected", [
    ("https://www.twitch.tv/videos/123?t=30s", ("twitch", "123", "https://www.twitch.tv/videos/123")),
    ("abc_def-ghI", ("youtube", "abc_def-ghI", "https://www.youtube.com/watch?v=abc_def-ghI")),
])
def test_source_identity(value, expected):
    assert sources.source_url(value) == expected


@pytest.mark.parametrize("value", ["https://evil.invalid/videos/123", "https://twitch.tv/vaarattu",
                                    "https://twitch.tv:123/videos/123", "file:///tmp/video"])
def test_source_rejects_other_destinations(value):
    with pytest.raises(ValueError):
        sources.source_url(value)


def test_parts_sorted_by_number_not_upload_order():
    a = source(title="27.2.2026 - game (Part 1/2)")
    b = source("different01", "27.2.2026 - game (Part 2/2)")
    assert sources.ordered_parts([b, a]) == [a, b]
    for values in ([a], [a, a], [a, source(title="Other (Part 2/2)")], [a, source()]):
        with pytest.raises(ValueError):
            sources.ordered_parts(values)
    with pytest.raises(ValueError):
        sources.part_info("Recording (Part 0/2)")


def test_resolve_automatically_includes_sibling(settings, store, monkeypatch):
    a, b = source(title="Day (Part 1/2)"), source("different01", "Day (Part 2/2)")
    store.save_video_page({"id": settings.youtube_channel_id}, [dict(x, published="date") for x in (b, a)])
    lookup = {x["url"]: x for x in (a, b)}
    monkeypatch.setattr(sources, "metadata", lambda settings, url, folder: dict(lookup[url]))
    result = sources.resolve(settings, store, [b["url"]], settings.work / "test")
    assert [s["id"] for s in result["sources"]] == [a["id"], b["id"]]
    assert [s["asset"] for s in result["sources"]] == ["s0", "s1"]
    assert result["notes"]
    assert store.runs() == []


def test_twitch_audio_extracts_from_muxed_source(settings, monkeypatch):
    calls = []

    def run(args, settings, folder, name, check, **kw):
        calls.append(args)
        path = folder / "source.opus"
        path.write_bytes(b"audio")
        (folder / "download.txt").write_text(str(path))

    monkeypatch.setattr(sources, "run_tool", run)
    monkeypatch.setattr(sources.youtube, "probe", lambda *args: {"streams": [{"codec_type": "audio"}]})
    sources.acquire(settings, {"url": "https://www.twitch.tv/videos/123"}, settings.work / "audio", lambda: None)
    assert "bestaudio/best" in calls[0] and "-x" in calls[0]
    assert calls[0][-1] == "https://www.twitch.tv/videos/123"


def test_gaps_are_kept_unless_explicitly_shortened():
    items = edit.units({"s0": transcript()})
    original = edit.Edit(spans=[edit.Span(first="u0", last="u3")], gaps=[], reason="Keep story")
    kept = edit.compile_edit(original, items)
    assert len(kept) == 1
    original.gaps = [edit.Gap(id="gu0:u1", action="shorten", reason="Waiting with no new discussion")]
    cut = edit.compile_edit(original, items)
    assert len(cut) == 2
    assert cut[0]["end_us"] == items[0]["speech_end_us"]+300000
    assert cut[1]["start_us"] == items[1]["speech_start_us"]-300000
    assert sum(s["end_us"]-s["start_us"] for s in cut) < kept[0]["end_us"]-kept[0]["start_us"]


def test_invalid_cuts_gaps_timing_and_duration_fail():
    items = edit.units({"s0": transcript(), "s1": transcript()})
    for spans, gaps in [([("u2", "u1")], []), ([("u0", "u21")], []),
                         ([("u0", "u4"), ("u3", "u5")], []), ([("unknown", "u1")], []),
                         ([("u0", "u3")], [edit.Gap(id="gu4:u5", action="shorten", reason="bad")])]:
        proposal = edit.Edit(spans=[edit.Span(first=a, last=b) for a, b in spans], gaps=gaps, reason="test")
        with pytest.raises(ModelAnchorError):
            edit.compile_edit(proposal, items)
    proposal = edit.Edit(spans=[edit.Span(first="u0", last="u4")], gaps=[], reason="test")
    with pytest.raises(ModelAnchorError):
        edit.compile_edit(proposal, items, allowance=5)
    items[0]["unsafe"] = True
    with pytest.raises(ModelAnchorError):
        edit.compile_edit(proposal, items)


class Evaluator:
    discovery_budget = 48000
    discovery_reasoning = "low"
    verification_reasoning = "medium"

    def __init__(self, issues=False):
        self.calls = []
        self.issues = issues

    def request_size(self, system, prompt, schema):
        return len(prompt)

    def check(self):
        pass

    def report(self, message):
        pass

    def call(self, system, prompt, schema, key, validate=None, **kw):
        payload = json.loads(prompt)
        self.calls.append((key, payload, kw))
        if schema is edit.Scan:
            value = edit.Scan(beats=[edit.Beat(first="u1", last="u7", value=4, reason="Developed story", continuation="")])
        elif schema is edit.Picks:
            value = edit.Picks(ids=["b0"])
        elif schema is edit.Edit:
            value = edit.Edit(spans=[edit.Span(first="u1", last="u7")], gaps=[], reason="Complete story")
        else:
            value = edit.Review(issues=[edit.Issue(sequence="b0", instruction="Retain payoff")] if self.issues else [])
        if validate:
            validate(value)
        return value


def test_planner_and_bounded_no_change_review():
    items = edit.units({"s0": transcript()})
    evaluator = Evaluator(issues=True)
    result = edit.plan(items, evaluator, lambda p: None, target=300)
    assert len(result["retained"]) == 1
    assert result["issues"]
    assert len([k for k, _, _ in evaluator.calls if k.startswith("revise-")]) == 1
    assert len(result["review_history"]) == 1
    assert evaluator.calls[0][2]["reasoning_effort"] == "low"
    critic = next(p for k, p, _ in evaluator.calls if k.startswith("critic-"))
    assert "retained_ranges" in critic["assembled"][0]
    assert "Passage 1." in json.dumps(critic)
    assert result["shorter_than_target"]


def test_user_revision_does_not_rescan():
    items = edit.units({"s0": transcript()})
    initial = edit.plan(items, Evaluator(), lambda p: None, 300)
    evaluator = Evaluator()
    edit.plan(items, evaluator, lambda p: None, 300, initial, "Remove the routine explanation")
    assert not any(k.startswith(("scan-", "shortlist-")) for k, _, _ in evaluator.calls)
    payload = next(p for k, p, _ in evaluator.calls if k.startswith("edit-"))
    assert payload["guidance"] == "Remove the routine explanation"
    assert payload["previous_edit"]


def test_jobs_are_isolated_and_controls_guard_revisions(settings, store):
    separate = highlights.store_for(settings)
    run_id = separate.admit(config(), "highlights")
    assert store.runs() == []
    assert separate.claim() == run_id
    separate.update(run_id, state="completed", result={"revision": 1, "has_draft": True,
        "history": [{"revision": 1, "has_draft": True}]})
    with pytest.raises(ValueError):
        highlights.control(separate, run_id, "approve", 2)
    highlights.control(separate, run_id, "approve", 1)
    assert separate.get(run_id)["result"]["approved_revision"] == 1
    with pytest.raises(ValueError):
        highlights.control(separate, run_id, "revise", 1, "Remove filler")
    separate.update(run_id, state="completed")
    highlights.control(separate, run_id, "revise", 1, "Remove filler")
    result = separate.get(run_id)["result"]
    assert result["revision"] == 2 and result["approved_revision"] is None
    separate.update(run_id, state="completed", result={**result, "history": [
        {"revision": 1, "has_draft": True}, {"revision": 2, "has_draft": True}]})
    highlights.control(separate, run_id, "restore", 2, restore=1)
    highlights.control(separate, run_id, "revise", 1, "Different edit")
    assert separate.get(run_id)["result"]["revision"] == 3
    assert store.runs() == []


def test_highlights_api_local_boundary_and_final_approval(settings, monkeypatch):
    monkeypatch.setattr("vaarattu_shorts.web.preflight", lambda *args: {})
    monkeypatch.setattr("vaarattu_shorts.web.codex_settings", lambda: {"model": "test"})
    monkeypatch.setattr(sources, "resolve", lambda *args: config()["manifest"])
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/highlights/resolve", json={"urls": [source()["url"]]}).status_code == 403
        token = client.get("/api/status").json()["token"]
        headers = {"X-Local-Token": token, "Idempotency-Key": "highlight-api-test"}
        manifest = client.post("/api/highlights/resolve", headers=headers, json={"urls": [source()["url"]]}).json()
        result = client.post("/api/highlights", headers=headers, json={"manifest_id": manifest["id"]})
        assert result.status_code == 202, result.text
        run_id = result.json()["id"]
        assert client.get("/api/runs").json() == []
        assert len(client.get("/api/highlights").json()) == 1
        assert client.post(f"/api/highlights/{run_id}/approve", headers=headers, json={"revision": 1}).status_code == 400
        assert client.get(f"/api/highlights/{run_id}/video?revision=1&quality=final").status_code == 404
        assert client.post("/api/highlights", headers=headers, json={"manifest_id": "../escape"}).status_code == 422


def test_pipeline_resume_final_and_revision(settings, store, monkeypatch):
    separate = highlights.store_for(settings)
    run_id = separate.admit(config(), "pipeline")
    events = []

    def acquire(settings, source, folder, check, interval=None):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("source.mkv" if interval else "source.opus")
        path.write_bytes(b"media")
        events.append("section" if interval else "audio")
        return path

    def transcribe(settings, path, duration, profile, folder, check, progress):
        events.append("transcribe")
        value = transcript()
        atomic_json(folder / "transcript.json", value)
        return value

    def render(settings, plan, media, output, encoder, check, progress):
        events.append(output.stem)
        output.write_bytes(b"rendered")
        return {"duration": plan["duration"], "sha256": "test"}

    monkeypatch.setattr(sources, "acquire", acquire)
    monkeypatch.setattr(sources, "reusable_transcript", lambda *args: None)
    monkeypatch.setattr(highlights.youtube, "probe", lambda *args: {"format": {"duration": "120"}})
    monkeypatch.setattr(highlights.transcribe, "transcribe", transcribe)
    monkeypatch.setattr(highlights.render, "align", lambda *args, **kw: {"origin_us": 0, "section_duration": 120})
    monkeypatch.setattr(highlights, "Evaluator", lambda *args, **kw: Evaluator())
    monkeypatch.setattr(highlights, "render_video", render)
    separate.claim()
    first = highlights.Highlights(settings, separate, run_id).execute()
    assert first["has_draft"] and not first["has_final"]
    assert events == ["audio", "transcribe", "section", "draft"]
    assert store.runs() == []
    assert not (settings.work / "runs" / run_id).exists()
    highlights.control(separate, run_id, "approve", 1)
    separate.claim()
    monkeypatch.setattr(edit, "VERSION", "a-newer-prompt-must-not-change-an-approved-edit")
    highlights.Highlights(settings, separate, run_id).execute()
    assert events == ["audio", "transcribe", "section", "draft", "final"]
    highlights.control(separate, run_id, "revise", 1, "Keep the story")
    separate.claim()
    result = highlights.Highlights(settings, separate, run_id).execute()
    assert result["revision"] == 2
    assert events.count("audio") == events.count("transcribe") == 1
    assert len(result["history"]) == 2
    assert (highlights.revision_folder(settings, run_id, 1) / "final.mp4").exists()


def test_recovery_and_transient_failure_preserve_checkpoints(settings, monkeypatch):
    separate = highlights.store_for(settings)
    run_id = separate.admit(config(), "resume")
    separate.claim()
    separate.recover()
    assert separate.get(run_id)["state"] == "queued"
    monkeypatch.setattr(highlights.Highlights, "execute", lambda self: (_ for _ in ()).throw(ValueError("Retry later")))
    highlights.run_job(settings, separate, run_id, lambda: False)
    assert separate.get(run_id)["state"] == "failed"
    highlights.control(separate, run_id, "resume", 1)
    assert separate.get(run_id)["state"] == "queued"


def test_adjacent_ranges_cannot_implicitly_cut_a_pause():
    items = edit.units({"s0": transcript()})
    proposal = edit.Edit(spans=[edit.Span(first="u0", last="u0"), edit.Span(first="u1", last="u1")],
                         gaps=[], reason="Keep both passages")
    kept = edit.compile_edit(proposal, items)
    assert len(kept) == 1
    assert kept[0]["start_us"] == items[0]["start_us"]
    assert kept[0]["end_us"] == items[1]["end_us"]
    proposal.gaps = [edit.Gap(id="gu0:u1", action="shorten", reason="An explicitly dispensable wait")]
    assert len(edit.compile_edit(proposal, items)) == 2


def test_final_render_rejects_changed_approved_plan_before_any_ai(settings):
    separate = highlights.store_for(settings)
    run_id = separate.admit(config(), "changed-plan")
    folder = highlights.revision_folder(settings, run_id, 1)
    atomic_json(folder / "plan.json", {"tampered": True})
    separate.update(run_id, state="running", result={"revision": 1, "mode": "final",
                    "approved_revision": 1, "plan_sha256": "original-approved-hash"})
    with pytest.raises(ValueError, match="approved edit"):
        highlights.Highlights(settings, separate, run_id).execute()
