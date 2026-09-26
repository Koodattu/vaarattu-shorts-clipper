from vaarattu_shorts import highlight_pauses as pauses
import json

import httpx
import pytest

from vaarattu_shorts import highlight_review as review
from fastapi.testclient import TestClient

from vaarattu_shorts import highlight_edit as edit, highlight_episode as episode, highlight_sources as sources, highlights
from vaarattu_shorts.llm import Evaluator as ModelEvaluator, ModelAnchorError, ModelOutputError
from vaarattu_shorts.processes import Interrupted
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
    verification_budget = 48000
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
        if schema is highlights.highlight_copy.VideoCopy:
            value = schema(title="A specific story", caption="A short description.")
        elif schema is edit.Scan:
            value = edit.Scan(beats=[edit.Beat(first="u1", last="u7", value=4, reason="Developed story", continuation="")])
        elif issubclass(schema, (episode.EditBatch, pauses.Batch)):
            value = schema(scenes=[dict(id=c["scene"]["id"],
                spans=[edit.Span(first="u1", last="u7")], pauses=[], value=3, reason="Complete story") for c in payload["scenes"]])
        elif schema is episode.Proposal:
            value = episode.Proposal(spans=[edit.Span(first="u1", last="u7")], pauses=[], value=3, reason="Complete story")
        elif schema is episode.SceneEdit:
            value = episode.SceneEdit(spans=[edit.Span(first="u1", last="u7")], gaps=[], value=3, reason="Complete story")
        elif schema is review.Duplicates:
            value = review.Duplicates(duplicates=[])
        elif schema is review.Decisions:
            value = review.Decisions(scenes=[review.Decision(sequence=c["sequence"], verdict="acceptable",
                remove=[], restore=[], pauses=[], protect=[], reason="The scene works.") for c in payload["scenes"]])
        elif schema is review.Checks:
            value = review.Checks(scenes=[review.Check(sequence=c["sequence"], verdict="resolved", reason="The change preserves context.") for c in payload["scenes"]])
        elif schema is episode.Ratings:
            value = episode.Ratings(scenes=[episode.Rating(id=c["id"], score=80, reason="Enjoyable scene") for c in payload["scenes"]])
        elif schema is episode.EpisodeReview:
            value = episode.EpisodeReview(context=[], duplicates=[], issues=[edit.Issue(sequence="b0", instruction="Retain payoff")] if self.issues else [])
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
    monkeypatch.setattr("vaarattu_shorts.web.codex_settings", lambda model=None: {"model": model or "test"})
    monkeypatch.setattr(sources, "resolve", lambda *args, **kwargs: config()["manifest"])
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


@pytest.mark.parametrize("critic_failure", [False, True])
@pytest.mark.parametrize("copy_failure", [False, True])
def test_pipeline_resume_final_and_revision(settings, store, monkeypatch, critic_failure, copy_failure):
    separate = highlights.store_for(settings)
    run_id = separate.admit({**config(), "discovery_reasoning": "none", "verification_reasoning": "xhigh"}, "pipeline")
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
    monkeypatch.setattr(sources, "acquire_aligned", lambda settings, source, folder, check, interval, audio, spans: (acquire(settings, source, folder, check, interval), {"origin_us": 0, "section_duration": 120}))
    class PipelineEvaluator(Evaluator):
        def call(self, system, prompt, schema, key, **kw):
            if critic_failure and schema in {edit.Review, episode.EpisodeReview, review.Decisions, review.Checks}:
                raise ModelOutputError("Invalid critique after retries")
            return super().call(system, prompt, schema, key, **kw)

    reasoning = []
    def evaluator(*args, **kw):
        efforts = (kw["discovery_reasoning"], kw["verification_reasoning"])
        if args[3].name == "inference":
            reasoning.append(efforts)
        result = PipelineEvaluator()
        result.discovery_reasoning, result.verification_reasoning = efforts
        return result

    monkeypatch.setattr(highlights, "Evaluator", evaluator)
    monkeypatch.setattr(highlights, "render_video", render)
    if copy_failure:
        monkeypatch.setattr(highlights.highlight_copy, "propose", lambda *a: (_ for _ in ()).throw(RuntimeError("copy service failure")))
    separate.claim()
    first = highlights.Highlights(settings, separate, run_id).execute()
    assert first["has_draft"] and not first["has_final"]
    visible = highlights.public(settings, separate, separate.get(run_id))
    assert separate.get(run_id)["state"] == "completed"
    assert bool(visible["publishing_copy"].get("error")) == copy_failure
    if not copy_failure:
        assert visible["publishing_copy"]["title"] == "A specific story"
    assert bool(first["issues"]) == critic_failure
    if critic_failure:
        assert "could not be verified" in first["issues"][0]["instruction"]
    assert visible["warnings"] == first["warnings"] == first["history"][0]["warnings"]
    assert events == ["audio", "transcribe", "section", "draft"]
    assert store.runs() == []
    assert not (settings.work / "runs" / run_id).exists()
    highlights.control(separate, run_id, "approve", 1)
    separate.claim()
    monkeypatch.setattr(episode, "VERSION", "a-newer-prompt-must-not-change-an-approved-edit")
    highlights.Highlights(settings, separate, run_id).execute()
    assert events == ["audio", "transcribe", "section", "draft", "final"]
    highlights.control(separate, run_id, "revise", 1, "Keep the story")
    separate.claim()
    result = highlights.Highlights(settings, separate, run_id).execute()
    assert result["revision"] == 2
    assert reasoning == [("none", "xhigh"), ("none", "xhigh")]
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


@pytest.fixture
def model_replies(settings, store, monkeypatch):
    run = store.admit({"provider": "codex"}, "highlight-recovery")
    replies, requests, waits = [], [], []

    def handle(request):
        requests.append(json.loads(request.content))
        value = replies.pop(0)
        if isinstance(value, int):
            return httpx.Response(value)
        return httpx.Response(200, json={"status": "completed", "output": [
            {"content": [{"type": "output_text", "text": value if isinstance(value, str) else json.dumps(value)}]}],
            "usage": {"input_tokens": 100, "output_tokens": 10}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = ModelEvaluator("codex", store, run, settings.work, 0, lambda: None, client)
        monkeypatch.setattr(evaluator, "wait_for_retry", lambda reason, delay, retry, total=None: waits.append((delay, retry, total)))
        yield evaluator, replies, requests, waits


def beat(first="u1", last="u7"):
    return {"first": first, "last": last, "value": 4, "reason": "Complete story", "continuation": ""}


def test_context_only_proposals_defer_and_cross_boundary_setup_is_preserved(model_replies):
    evaluator, replies, requests, waits = model_replies
    original = transcript()
    original["words"] = [dict(original["words"][0], id=f"w{i}", start_us=i*5000000+1000000,
                              end_us=i*5000000+3000000) for i in range(120)]
    original["duration_us"] = 600000000
    items = edit.units({"s0": original})
    replies.extend([{"beats": [beat("u75", "u80"), beat("u65", "u76")]},
                    {"beats": [beat("u65", "u80"), beat("u95", "u100")]}])
    progress, warnings = [], []
    result = edit.discover(items, evaluator, progress.append, warnings)
    assert [(b["first"], b["last"]) for b in result] == [("u65", "u80"), ("u95", "u100")]
    assert len(requests) == 2 and not waits and not warnings
    assert progress[-1] == 1


def test_invalid_highlight_retries_with_specific_feedback_and_reuses_valid_cache(model_replies):
    evaluator, replies, requests, waits = model_replies
    replies.extend([{"beats": [beat("invented", "u7")]}, "broken JSON", {"beats": [beat()]}])
    items = edit.units({"s0": transcript()})
    warnings = []
    expected = edit.discover(items, evaluator, lambda _: None, warnings)
    assert edit.discover(items, evaluator, lambda _: None, warnings) == expected
    assert len(requests) == 3 and len(expected) == 1 and not warnings
    assert waits == [(5, 1, 2), (15, 2, 2)]
    assert "invented" in requests[1]["input"] and "Validation problem" in requests[1]["input"]
    assert requests[2]["input"].count("Validation problem") == 1


def test_scan_keeps_verified_proposals_after_persistent_bad_anchors(model_replies):
    evaluator, replies, requests, waits = model_replies
    replies.extend([{"beats": [beat(), beat("invented", "u7")]}]*3)
    warnings = []
    result = edit.discover(edit.units({"s0": transcript()}), evaluator, lambda _: None, warnings)
    assert len(result) == 1 and result[0]["first"] == "u1"
    assert len(requests) == 3 and len(waits) == 2
    assert len(warnings) == 1 and "could not be fully analyzed" in warnings[0]


def test_failed_scan_does_not_block_later_sections(model_replies):
    evaluator, replies, requests, waits = model_replies
    original = transcript()
    original["duration_us"] = 900000000
    original["words"] += [dict(w, id="later-"+w["id"], start_us=w["start_us"]+720000000,
                               end_us=w["end_us"]+720000000) for w in original["words"]]
    replies.extend(["invalid"]*3 + [{"beats": [beat("u21", "u27")]}])
    warnings = []
    result = edit.discover(edit.units({"s0": original}), evaluator, lambda _: None, warnings)
    assert len(result) == 1 and result[0]["first"] == "u21"
    assert len(requests) == 4 and len(waits) == 2 and len(warnings) == 1


def test_failed_ranking_keeps_only_known_unique_choices(model_replies):
    evaluator, replies, requests, waits = model_replies
    replies.extend([{"ids": ["invented", "b0", "b0"]}]*3)
    warnings = []
    candidate = dict(beat(), id="b0")
    result = edit.shortlist([candidate], edit.units({"s0": transcript()}), evaluator, 300, warnings)
    assert result == [candidate] and len(warnings) == 1 and len(requests) == 3


@pytest.mark.parametrize("previous", [False, True])
def test_invalid_edit_never_renders_hallucinated_cuts(model_replies, previous):
    evaluator, replies, requests, waits = model_replies
    replies.extend([{"spans": [{"first": "u7", "last": "u1"}], "gaps": [], "reason": "Bad cut"}]*3)
    old = {"spans": [{"first": "u1", "last": "u7"}], "gaps": [], "reason": "Safe edit"} if previous else None
    warnings = []
    items = edit.units({"s0": transcript()})
    result = edit.edit_sequence(dict(beat(), id="b0"), items, evaluator, 300, "edit-test", previous=old, warnings=warnings)
    if previous:
        assert result.model_dump() == old
        assert edit.compile_edit(result, items)[0]["first"] == "u1"
    else:
        assert edit.compile_edit(result, items) == []
    assert len(warnings) == 1 and len(requests) == 3


def test_invalid_critic_keeps_valid_timeline_and_surfaces_warning(model_replies):
    evaluator, replies, requests, waits = model_replies
    replies.extend([{"beats": [beat()]}, {"ids": ["b0"]},
                    {"spans": [{"first": "u1", "last": "u7"}], "gaps": [], "reason": "Safe edit"},
                    *[{"issues": [{"sequence": "invented", "instruction": "Change edit"}]}]*3])
    result = edit.plan(edit.units({"s0": transcript()}), evaluator, lambda _: None, 300)
    assert len(result["retained"]) == 1
    assert len(result["warnings"]) == 1 and "review could not be completed" in result["warnings"][0]
    assert len(requests) == 6


def test_no_verified_edit_is_recoverable_pause_not_empty_success(settings, model_replies, monkeypatch):
    evaluator, replies, requests, waits = model_replies
    replies.extend(["invalid"]*3)
    separate = highlights.store_for(settings)
    run_id = separate.admit(config(), "invalid-output")
    separate.claim()
    monkeypatch.setattr(highlights.Highlights, "execute", lambda self: edit.plan(
        edit.units({"s0": transcript()}), evaluator, lambda _: None, 300))
    highlights.run_job(settings, separate, run_id, lambda: False)
    run = separate.get(run_id)
    assert run["state"] == "paused" and "No verified highlight edit" in run["message"]
    assert len(requests) == 3
    highlights.control(separate, run_id, "resume", 1)
    assert separate.get(run_id)["state"] == "queued"


@pytest.mark.parametrize("failure", [ValueError("Spending limit reached"), Interrupted("cancel")])
def test_recovery_does_not_swallow_budget_or_cancellation(failure):
    evaluator = Evaluator()
    evaluator.call = lambda *args, **kw: (_ for _ in ()).throw(failure)
    with pytest.raises(type(failure)):
        edit.discover(edit.units({"s0": transcript()}), evaluator, lambda _: None, [])


def test_auth_failure_does_not_become_an_empty_scan(model_replies):
    evaluator, replies, requests, waits = model_replies
    replies.append(401)
    with pytest.raises(ValueError, match="HTTP 401"):
        edit.discover(edit.units({"s0": transcript()}), evaluator, lambda _: None, [])
    assert len(requests) == 1 and not waits


def test_invalid_cached_suggestion_is_regenerated(model_replies, settings):
    evaluator, replies, requests, waits = model_replies
    replies.extend([{"beats": [beat()]}, {"beats": [beat()]}])
    items = edit.units({"s0": transcript()})
    expected = edit.discover(items, evaluator, lambda _: None)
    cache = next(p for p in settings.work.glob("scan-0-*.json") if len(p.name.split(".")) == 2)
    cache.write_text('{"beats":[{"bad":"shape"}]}', encoding="utf-8")
    assert edit.discover(items, evaluator, lambda _: None) == expected
    assert len(requests) == 2
    assert json.loads(cache.read_text(encoding="utf-8"))["beats"][0]["first"] == "u1"


def test_invalid_revision_keeps_last_valid_edit_and_unresolved_issue(model_replies):
    evaluator, replies, requests, waits = model_replies
    valid = {"spans": [{"first": "u1", "last": "u7"}], "gaps": [], "reason": "Safe edit"}
    replies.extend([{"beats": [beat()]}, {"ids": ["b0"]}, valid,
                    {"issues": [{"sequence": "b0", "instruction": "Tighten the detour"}]},
                    *["invalid"]*3])
    result = edit.plan(edit.units({"s0": transcript()}), evaluator, lambda _: None, 300)
    assert result["sequences"][0]["edit"] == valid
    assert result["issues"][0]["instruction"] == "Tighten the detour"
    assert len(result["review_history"]) == 1 and len(requests) == 7
    assert "previous verified edit was kept" in result["warnings"][0]


def test_cancel_during_output_repair_stops_before_next_request(model_replies, monkeypatch):
    evaluator, replies, requests, waits = model_replies
    replies.extend(["invalid", {"beats": [beat()]}])
    monkeypatch.setattr(evaluator, "wait_for_retry", lambda *args, **kw: (_ for _ in ()).throw(Interrupted("cancel")))
    with pytest.raises(Interrupted):
        edit.discover(edit.units({"s0": transcript()}), evaluator, lambda _: None, [])
    assert len(requests) == 1


def test_scene_repair_explains_evidence_ids_instead_of_repeating_generic_error(model_replies):
    evaluator, replies, requests, waits = model_replies
    rows = [{"id": f"u{i}", "asset": "s0", "start_us": i*10000000,
             "end_us": i*10000000+2000000, "speech_start_us": i*10000000,
             "speech_end_us": i*10000000+2000000, "unsafe": False, "text": "A thought."} for i in range(2)]
    bad = {"spans": [{"first": "u0", "last": "u1"}], "pauses": [
        {"id": "gu0:u1", "action": "keep", "evidence": "A thought.", "reason": "Comic timing"}],
        "value": 3, "reason": "Enjoyable exchange"}
    good = json.loads(json.dumps(bad))
    good["pauses"][0]["evidence"] = "u1"
    replies.extend([bad, good])
    result = episode.compact({"first": "u0", "last": "u1"}, rows, evaluator, "repair-evidence")
    assert result.gaps[0].evidence == "u1"
    assert "evidence must be a retained passage ID such as u1, not quoted speech" in requests[1]["input"]
    assert waits == [(5, 1, 2)]
    episode.compact({"first": "u0", "last": "u1"}, rows, evaluator, "repair-evidence")
    assert len(requests) == 2


def test_scene_repair_supplies_complete_gap_id(model_replies):
    evaluator, replies, requests, waits = model_replies
    rows = [{"id": f"u{i}", "asset": "s0", "start_us": i*10000000,
             "end_us": i*10000000+2000000, "speech_start_us": i*10000000,
             "speech_end_us": i*10000000+2000000, "unsafe": False, "text": "A thought."} for i in range(2)]
    bad = {"spans": [{"first": "u0", "last": "u1"}], "pauses": [
        {"id": "gu0", "action": "lead_in", "seconds": 3, "reason": "Reaction"}],
        "value": 3, "reason": "Enjoyable exchange"}
    good = json.loads(json.dumps(bad))
    good["pauses"][0]["id"] = "gu0:u1"
    replies.extend([bad, good])
    result = episode.compact({"first": "u0", "last": "u1"}, rows, evaluator, "repair-gap")
    assert result.gaps[0].id == "gu0:u1"
    assert "Available IDs: gu0:u1" in requests[1]["input"]


def test_resume_repairs_one_scene_without_regenerating_verified_batch_neighbors(model_replies):
    evaluator, replies, requests, waits = model_replies
    rows = [{"id": f"u{i}", "asset": "s0", "start_us": i*10000000,
             "end_us": i*10000000+2000000, "speech_start_us": i*10000000,
             "speech_end_us": i*10000000+2000000, "unsafe": False, "text": "A thought."} for i in range(4)]
    beats = [{"id": "a", "first": "u0", "last": "u1"}, {"id": "b", "first": "u2", "last": "u3"}]
    good = {"spans": [{"first": "u0", "last": "u1"}], "pauses": [], "value": 3, "reason": "Enjoyable exchange"}
    # The ID exists in the supplied context, but its speech is not retained in scene b.
    bad_pause = {"gap_ref": "g3", "action": "keep", "evidence_passage_id": "u0", "reason": "Comic timing"}
    bad = {"spans": [{"first": "u2", "last": "u3"}], "pauses": [bad_pause], "value": 3, "reason": "Enjoyable exchange"}
    replies.extend([{"scenes": [{"id": "a", **good}, {"id": "b", **bad}]},
                    {"repairs": [{"slot": 0, "pause": bad_pause}]}])
    warnings = []
    first = episode.edit_scenes(beats, rows, evaluator, "edit", "", warnings)
    assert len(requests) == 2 and not waits
    assert warnings and first[1]["pause_recovery"]["fallback_gaps"] == ["gu2:u3"]
    assert first[0]["edit"]["spans"] == good["spans"]
    assert first[1]["edit"]["spans"] == bad["spans"]
    retained = episode.compiled(first[1], rows)
    assert any(s["start_us"] <= rows[2]["speech_end_us"] < rows[3]["speech_start_us"] <= s["end_us"] for s in retained)
    for candidate in first:
        for u in edit.sequence_items(candidate["beat"], rows):
            if candidate["beat"]["first"] <= u["id"] <= candidate["beat"]["last"]:
                assert any(s["start_us"] <= u["speech_start_us"] < u["speech_end_us"] <= s["end_us"] for s in episode.compiled(candidate, rows))
    corrected = {**bad_pause, "evidence_passage_id": "u3"}
    replies.append({"repairs": [{"slot": 0, "pause": corrected}]})
    result = episode.edit_scenes(beats, rows, evaluator, "edit", "", [])
    assert [s["beat"]["id"] for s in result] == ["a", "b"]
    assert result[0] == first[0]
    assert result[1]["edit"]["spans"] == bad["spans"]
    assert result[1]["pause_recovery"] == {"repaired_instructions": 1, "fallback_gaps": []}
    assert len(requests) == 3
    assert json.loads(requests[-1]["input"])["original_proposal"]["spans"] == bad["spans"]


@pytest.mark.parametrize("passages", [(16, 17, 18), (735, 736, 737), (2397, 2398, 2399)])
def test_scene_repair_reports_gap_and_evidence_faults_together(model_replies, passages):
    evaluator, replies, requests, waits = model_replies
    rows = [{"id": f"u{ident}", "asset": "s0", "start_us": i*10000000,
             "end_us": i*10000000+2000000, "speech_start_us": i*10000000,
             "speech_end_us": i*10000000+2000000, "unsafe": False, "text": "A thought."}
            for i, ident in enumerate(passages)]
    first, middle, last = [r["id"] for r in rows]
    bad = {"spans": [{"first": first, "last": last}], "pauses": [
        {"id": f"g{first}", "action": "keep", "evidence": "A thought.", "reason": "Comic timing"},
        {"id": f"g{middle}", "action": "keep", "evidence": "Another quote.", "reason": "Reaction"}],
        "value": 3, "reason": "Enjoyable exchange"}
    good = json.loads(json.dumps(bad))
    for pause, left, right in zip(good["pauses"], rows, rows[1:]):
        pause.update(id=f"g{left['id']}:{right['id']}", evidence=right["id"])
    replies.extend([bad, good])
    result = episode.compact({"first": first, "last": last}, rows, evaluator, "repair-both")
    repair = requests[1]["input"]
    for ident in [first, middle]:
        assert f"Unknown gap g{ident}." in repair
        assert f"For g{ident}, evidence must be a retained passage ID" in repair
    assert f"Available IDs: g{first}:{middle}, g{middle}:{last}" in repair
    assert result.spans == episode.Proposal.model_validate(bad).spans
    assert [g.evidence for g in result.gaps] == [middle, last]
    assert waits == [(5, 1, 2)]


def test_scene_repair_allows_second_delayed_retry_without_accepting_bad_evidence(model_replies):
    evaluator, replies, requests, waits = model_replies
    rows = [{"id": f"u{i}", "asset": "s0", "start_us": i*10000000,
             "end_us": i*10000000+2000000, "speech_start_us": i*10000000,
             "speech_end_us": i*10000000+2000000, "unsafe": False, "text": "A thought."} for i in range(2)]
    bad = {"spans": [{"first": "u0", "last": "u1"}], "pauses": [
        {"id": "gu0", "action": "keep", "evidence": "A thought.", "reason": "Comic timing"}],
        "value": 3, "reason": "Enjoyable exchange"}
    partial = json.loads(json.dumps(bad))
    partial["pauses"][0]["id"] = "gu0:u1"
    good = json.loads(json.dumps(partial))
    good["pauses"][0]["evidence"] = "u1"
    replies.extend([bad, partial, good])
    result = episode.compact({"first": "u0", "last": "u1"}, rows, evaluator, "repair-second")
    assert result.gaps[0].evidence == "u1"
    assert waits == [(5, 1, 2), (15, 2, 2)]
    assert len(requests) == 3
    episode.compact({"first": "u0", "last": "u1"}, rows, evaluator, "repair-second")
    assert len(requests) == 3


def test_upgrade_reuses_legacy_batch_and_targets_only_invalid_pause(model_replies):
    evaluator, replies, requests, waits = model_replies
    rows = [{"id": f"u{i}", "asset": "s0", "start_us": i*10000000,
             "end_us": i*10000000+2000000, "speech_start_us": i*10000000,
             "speech_end_us": i*10000000+2000000, "unsafe": False, "text": "A thought."} for i in range(4)]
    beats = [{"id": "a", "first": "u0", "last": "u1"}, {"id": "b", "first": "u2", "last": "u3"}]
    good = {"spans": [{"first": "u0", "last": "u1"}], "pauses": [], "value": 3, "reason": "Enjoyable exchange"}
    bad = {"spans": [{"first": "u2", "last": "u3"}], "pauses": [
        {"id": "gu2:u3", "action": "keep", "evidence": "A thought.", "reason": "Comic timing"}],
        "value": 3, "reason": "Enjoyable exchange"}
    replies.append({"scenes": [{"id": "a", **good}, {"id": "b", **bad}]})
    legacy = [{"scene": b, "speech": edit.speech_rows(edit.sequence_items(b, rows)), "previous_edit": None} for b in beats]
    edit.request(evaluator, episode.EDIT_RULES, {"scenes": legacy, "guidance": ""}, episode.EditBatch, "edit-0")
    replies.append({"repairs": [{"slot": 0, "pause": {"gap_ref": "g3", "action": "keep", "evidence_passage_id": "u3", "reason": "Comic timing"}}]})
    result = episode.edit_scenes(beats, rows, evaluator, "edit", "", [])
    assert [s["beat"]["id"] for s in result] == ["a", "b"]
    assert result[0]["edit"]["spans"] == good["spans"]
    assert result[1]["edit"]["spans"] == bad["spans"]
    assert result[1]["edit"]["gaps"][0]["evidence"] == "u3"
    assert len(requests) == 2 and not waits
    assert json.loads(requests[-1]["input"])["original_proposal"]["pauses"] == bad["pauses"]
    assert episode.edit_scenes(beats, rows, evaluator, "edit", "", []) == result
    assert len(requests) == 2
