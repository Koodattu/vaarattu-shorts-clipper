import json

import pytest

from vaarattu_shorts import highlight_review as review

from vaarattu_shorts import highlight_edit as source, highlight_episode as episode, highlights
from vaarattu_shorts.llm import ModelAnchorError


def items(count=18):
    return [{"id": f"u{i}", "asset": "s0", "start_us": i*10000000+700000,
             "end_us": i*10000000+3300000, "speech_start_us": i*10000000+1000000,
             "speech_end_us": i*10000000+3000000, "text": f"Thought {i}.",
             "unsafe": False, "gap_unsafe": False} for i in range(count)]


def edit(first="u0", last="u2", gaps=None, value=3):
    return episode.SceneEdit(spans=[source.Span(first=first, last=last)], gaps=gaps or [],
                             value=value, reason="Enjoyable commentary with unnecessary waiting removed.")


def scene(ident, first, last):
    return {"beat": {"id": ident, "first": first, "last": last, "value": 2,
                     "reason": "Entertaining exchange", "continuation": ""},
            "edit": edit(first, last).model_dump()}


def test_default_pause_compression_preserves_every_word_and_short_pauses():
    rows = items(3)
    rows[1].update(start_us=4300000, speech_start_us=4600000, speech_end_us=6000000, end_us=6300000)
    retained = episode.compile_scene(edit(), rows)
    assert len(retained) == 2
    assert retained[0]["start_us"] == rows[0]["start_us"]
    assert retained[0]["end_us"] == 6400000
    assert retained[1]["start_us"] == 20600000
    assert retained[1]["first"] == "u2" and retained[0]["last"] == "u1"
    for u in rows:
        assert any(s["start_us"] <= u["speech_start_us"] < u["speech_end_us"] <= s["end_us"] for s in retained)


def test_reaction_lead_in_keeps_only_requested_gameplay_before_reaction():
    rows = items(2)
    gap = episode.Pause(id="gu0:u1", action="lead_in", evidence="u1", seconds=4, reason="Following speech reacts to the preceding action.")
    retained = episode.compile_scene(edit(last="u1", gaps=[gap]), rows)
    assert retained[1]["start_us"] == rows[1]["speech_start_us"]-4000000
    assert retained[0]["end_us"] == rows[0]["speech_end_us"]+400000
    gap.action, gap.seconds = "keep", 0
    assert len(episode.compile_scene(edit(last="u1", gaps=[gap]), rows)) == 1


@pytest.mark.parametrize("fault", ["invented", "removed_evidence", "wrong_reaction", "unsafe", "duplicate"])
def test_pause_instructions_require_valid_retained_evidence_and_safe_timing(fault):
    rows = items(3)
    gap = episode.Pause(id="gu0:u1", action="lead_in", evidence="u1", seconds=4, reason="Reaction")
    gaps = [gap]
    if fault == "invented":
        gap.id = "gu0:madeup"
    elif fault == "removed_evidence":
        gap.evidence = "u2"
    elif fault == "wrong_reaction":
        gap.evidence = "u0"
    elif fault == "unsafe":
        rows[0]["gap_unsafe"] = True
    else:
        gaps.append(gap)
    with pytest.raises(ModelAnchorError):
        episode.compile_scene(edit(last="u1", gaps=gaps), rows)


def test_uncertain_gap_is_not_automatically_cut_and_legacy_edits_are_unchanged():
    rows = items(2)
    rows[0]["gap_unsafe"] = True
    assert len(episode.compile_scene(edit(last="u1"), rows)) == 1
    legacy = source.Edit(spans=[source.Span(first="u0", last="u1")], gaps=[], reason="Saved draft")
    rows[0]["gap_unsafe"] = False
    assert len(source.compile_edit(legacy, rows)) == 1
    assert len(episode.compile_scene(edit(last="u1"), rows)) == 2


def test_selection_keeps_all_worthwhile_scenes_and_removes_discarded_edits():
    rows = items(6)
    pool = [scene("a", "u0", "u1"), scene("b", "u2", "u3"), scene("c", "u4", "u5")]
    rankings = [{"id": ident, "score": score, "reason": "Specific content"} for ident, score in [("c", 80), ("a", 90), ("b", 85)]]
    length = episode.seconds(episode.compiled(pool[0], rows))
    selected, decisions = episode.assemble(pool, rankings, rows)
    assert [s["beat"]["id"] for s in selected] == ["a", "b", "c"]
    assert all(d["decision"] == "selected" for d in decisions)
    pool[0]["edit"].update(spans=[], value=1)
    selected, _ = episode.assemble(pool, rankings, rows)
    assert [s["beat"]["id"] for s in selected] == ["b", "c"]
    assert episode.seconds(episode.timeline(selected, rows)) <= length*2


def test_selection_rejects_duplicate_footage_and_weak_ratings():
    rows = items(6)
    pool = [scene("a", "u0", "u2"), scene("b", "u1", "u3"), scene("c", "u4", "u5")]
    rankings = [{"id": "a", "score": 90}, {"id": "b", "score": 80}, {"id": "c", "score": 45}]
    selected, decisions = episode.assemble(pool, rankings, rows)
    assert [s["beat"]["id"] for s in selected] == ["a"]
    assert [d["decision"] for d in decisions] == ["selected", "overlapping selected footage", "below editorial threshold"]


class Evaluator:
    discovery_budget = 48000
    discovery_reasoning = "low"
    verification_reasoning = "low"

    def __init__(self, revise=False):
        self.calls = []
        self.revise = revise

    def check(self):
        pass

    def report(self, message):
        pass

    def request_size(self, system, prompt, schema):
        return len(prompt)

    def call(self, system, prompt, schema, key, validate=None, **kw):
        payload = json.loads(prompt)
        self.calls.append((key, payload, kw))
        if schema is source.Scan:
            value = source.Scan(beats=[source.Beat(first=f"u{i}", last=f"u{i+1}", value=2, reason="Enjoyable commentary", continuation="") for i in (0, 6, 12)])
        elif schema is episode.EditBatch:
            value = episode.EditBatch(scenes=[episode.NamedProposal(id=c["scene"]["id"],
                spans=[source.Span(first=c["scene"]["first"], last=c["scene"]["last"])],
                pauses=[], value=3, reason="Enjoyable commentary") for c in payload["scenes"]])
        elif schema is episode.Proposal:
            beat = payload["scene"]
            value = episode.Proposal(spans=[source.Span(first=beat["first"], last=beat["last"])], pauses=[], value=3, reason="Enjoyable commentary")
            if key.startswith("episode-revise"):
                value.spans, value.value = [], 1
        elif schema is review.Duplicates:
            value = review.Duplicates(duplicates=[])
        elif schema is review.Decisions:
            value = review.Decisions(scenes=[review.Decision(sequence=c["sequence"], verdict="acceptable",
                remove=[], restore=[], pauses=[], protect=[], reason="The scene works.") for c in payload["scenes"]])
            if self.revise and key.startswith("episode-polish-0"):
                target = next(c for c in value.scenes if c.sequence == "b0")
                target.verdict = "change"
                target.remove = [source.Span(first="u0", last="u1")]
                target.reason = "This whole scene repeats the other exchange."
        elif schema is review.Checks:
            value = review.Checks(scenes=[review.Check(sequence=c["sequence"], verdict="resolved", reason="The change preserves context.") for c in payload["scenes"]])
        elif schema is episode.Ratings:
            value = episode.Ratings(scenes=[episode.Rating(id=c["id"], score=90-int(c["id"][1:]), reason="Grounded personality moment") for c in payload["scenes"]])
        else:
            value = episode.EpisodeReview(context=[], duplicates=[], issues=[source.Issue(sequence="b0", instruction="Remove this repetitive scene.")] if self.revise and key=="episode-critic-0-0" else [])
        if validate:
            validate(value)
        return value


def test_screening_precedes_batched_editing_and_review_removes_repetition():
    evaluator = Evaluator(revise=True)
    result = episode.plan(items(), evaluator, lambda _: None)
    keys = [k for k, _, _ in evaluator.calls]
    assert keys.index("episode-screen-0") < keys.index("episode-edit-0") < keys.index("episode-rank-0")
    assert len(next(p["scenes"] for k, p, _ in evaluator.calls if k == "episode-edit-0")) == 3
    assert result["metrics"]["edited_scenes"] == 3
    assert [s["beat"]["id"] for s in result["sequences"]] == ["b1", "b2"]
    assert any(r["sequence"] == "b0" and r["status"] == "applied" for r in result["review_history"])
    assert not result["issues"]
    assert result["duration"] <= 12
    assert evaluator.calls[0][2]["reasoning_effort"] == "low"
    assert all(kw["reasoning_effort"] == "low" for k, _, kw in evaluator.calls if not k.startswith("scan-"))
    assert result["rankings"] and result["selection"]


def test_user_revision_reuses_full_scene_pool_without_rescanning_or_mutating_parent():
    initial = episode.plan(items(), Evaluator(), lambda _: None)
    snapshot = json.dumps(initial, sort_keys=True)
    evaluator = Evaluator()
    revised = episode.plan(items(), evaluator, lambda _: None, previous=initial, guidance="Tighter exchanges")
    assert not any(k.startswith("scan-") for k, _, _ in evaluator.calls)
    assert len([k for k, _, _ in evaluator.calls if k.startswith("episode-edit")]) == 1
    assert len(next(p["scenes"] for k, p, _ in evaluator.calls if k == "episode-edit-0")) == 3
    assert len(revised["scene_pool"]) == 3
    assert json.dumps(initial, sort_keys=True) == snapshot


def test_old_style_revision_rebuilds_from_full_transcript():
    old = {"version": "highlights-v2", "beats": [], "sequences": [], "warnings": []}
    evaluator = Evaluator()
    result = episode.plan(items(), evaluator, lambda _: None, previous=old)
    assert any(k.startswith("scan-") for k, _, _ in evaluator.calls)
    assert result["version"] == episode.VERSION and len(result["scene_pool"]) == 3


def test_rebuild_creates_new_revision_and_retains_original_history(settings):
    store = highlights.store_for(settings)
    run = store.admit({"manifest": {"title": "Recording", "sources": []}}, "rebuild")
    store.update(run, state="completed", result={"revision": 1, "has_draft": True, "history": [{"revision": 1, "has_draft": True}]})
    highlights.control(store, run, "rebuild", 1)
    value = store.get(run)
    assert value["state"] == "queued"
    assert value["result"]["revision"] == 2 and value["result"]["parent_revision"] is None
    assert value["result"]["history"] == [{"revision": 1, "has_draft": True}]
    with pytest.raises(ValueError):
        highlights.control(store, run, "rebuild", 1)


def test_legacy_shortening_duration_is_harmless_and_removed_gap_is_ignored():
    rows = items(4)
    gap = episode.Pause(id="gu0:u1", action="shorten", evidence="u1", seconds=0.8, reason="Short pause")
    assert episode.compile_scene(edit(gaps=[gap]), rows) == episode.compile_scene(edit(), rows)
    removed = episode.Pause(id="gu2:u3", action="keep", evidence="u3", seconds=0, reason="Omitted scene")
    assert episode.compile_scene(edit(last="u1", gaps=[removed]), rows) == episode.compile_scene(edit(last="u1"), rows)


def test_new_schema_has_no_shorten_action_or_irrelevant_seconds():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        episode.Proposal(spans=[], pauses=[{"id": "gu0:u1", "action": "shorten", "seconds": 0.8}], value=3, reason="Edit")
    with pytest.raises(ValidationError):
        episode.KeepPause(id="gu0:u1", action="keep", evidence="u1", seconds=0.8, reason="Pause")
    proposal = episode.Proposal(spans=[source.Span(first="u0", last="u1")],
        pauses=[episode.LeadIn(id="gu0:u1", action="lead_in", seconds=4, reason="Following reaction")], value=3, reason="Story")
    compiled = episode.compile_scene(episode.verified_proposal(proposal, items(2)), items(2))
    assert compiled[1]["start_us"] == 7000000


def test_one_bad_batch_scene_repairs_only_that_scene(monkeypatch):
    real = Evaluator.call
    def call(self, system, prompt, schema, key, **kwargs):
        result = real(self, system, prompt, schema, key, **kwargs)
        if schema is episode.EditBatch:
            result.scenes[1].spans[0].first = "invented"
        return result
    monkeypatch.setattr(Evaluator, "call", call)
    evaluator = Evaluator()
    beats = [scene(f"b{i}", f"u{i*3}", f"u{i*3+1}")["beat"] for i in range(4)]
    result = episode.edit_scenes(beats, items(), evaluator, "batch", "", [])
    assert len(result) == 4
    assert [key for key, _, _ in evaluator.calls] == ["batch-0", "batch-repair-b1"]
    assert all(episode.compiled(s, items()) for s in result)


def test_missing_batch_scene_does_not_regenerate_successful_edits(monkeypatch):
    real = Evaluator.call
    def call(self, system, prompt, schema, key, **kwargs):
        result = real(self, system, prompt, schema, key, **kwargs)
        if schema is episode.EditBatch:
            result.scenes.pop()
        return result
    monkeypatch.setattr(Evaluator, "call", call)
    evaluator = Evaluator()
    beats = [scene(f"b{i}", f"u{i*3}", f"u{i*3+1}")["beat"] for i in range(3)]
    assert len(episode.edit_scenes(beats, items(), evaluator, "batch", "", [])) == 3
    assert [key for key, _, _ in evaluator.calls] == ["batch-0", "batch-repair-b2"]


def test_systemic_edit_failures_pause_before_processing_entire_recording(monkeypatch):
    from vaarattu_shorts.llm import ModelOutputError
    real = Evaluator.call
    def call(self, system, prompt, schema, key, **kwargs):
        if schema is episode.Proposal:
            self.calls.append((key, {}, {}))
            raise ModelOutputError("Invalid anchors")
        result = real(self, system, prompt, schema, key, **kwargs)
        if schema is episode.EditBatch:
            for proposal in result.scenes:
                proposal.spans[0].first = "invented"
        return result
    monkeypatch.setattr(Evaluator, "call", call)
    evaluator = Evaluator()
    beats = [scene(f"b{i}", f"u{i}", f"u{i}")["beat"] for i in range(18)]
    with pytest.raises(ModelOutputError, match="Several scene edits"):
        episode.edit_scenes(beats, items(), evaluator, "batch", "", [])
    assert len(evaluator.calls) == 4  # One batch and three targeted repairs, not 18 edits.


def test_every_eligible_scene_is_edited_without_a_scene_quota(monkeypatch):
    rows = items(220)
    beats = [scene(f"b{i}", f"u{i}", f"u{i}")["beat"] for i in range(220)]
    monkeypatch.setattr(source, "discover", lambda *a: beats)
    monkeypatch.setattr(episode, "screen_scenes", lambda *a: [{"id": b["id"], "score": 80} for b in beats])
    monkeypatch.setattr(episode, "rank_scenes", lambda pool, *a: [{"id": s["beat"]["id"], "score": 80} for s in pool])
    evaluator = Evaluator()
    result = episode.plan(rows, evaluator, lambda _: None)
    assert result["metrics"]["eligible_scenes"] == result["metrics"]["edited_scenes"] == 220
    assert len(result["sequences"]) == 220
    assert len([k for k, _, _ in evaluator.calls if k.startswith("episode-edit")]) == 55
    assert all(len(p["scenes"]) <= 4 for k, p, _ in evaluator.calls if k.startswith("episode-edit"))


def test_quality_filter_excludes_weak_scenes_without_filling_time(monkeypatch):
    rows = items(100)
    beats = [scene(f"b{i}", f"u{i}", f"u{i}")["beat"] for i in range(100)]
    monkeypatch.setattr(source, "discover", lambda *a: beats)
    monkeypatch.setattr(episode, "screen_scenes", lambda *a: [{"id": b["id"], "score": 80 if b["id"] == "b99" else 30} for b in beats])
    monkeypatch.setattr(episode, "rank_scenes", lambda pool, *a: [{"id": s["beat"]["id"], "score": 80} for s in pool])
    result = episode.plan(rows, Evaluator(), lambda _: None)
    assert result["metrics"]["edited_scenes"] == 1
    assert result["duration"] == 2.6
    assert [s["beat"]["id"] for s in result["sequences"]] == ["b99"]
    assert "shorter_than_target" not in result


def test_episode_longer_than_twenty_minutes_is_not_truncated():
    rows = items(500)
    pool = [scene(f"b{i}", f"u{i}", f"u{i}") for i in range(500)]
    selected, decisions = episode.assemble(pool, [{"id": s["beat"]["id"], "score": 80} for s in pool], rows)
    timeline = episode.timeline(selected, rows)
    assert len(selected) == 500 and all(d["decision"] == "selected" for d in decisions)
    assert episode.seconds(timeline) == 1300
    assert timeline[-1]["output_start_us"]+timeline[-1]["end_us"]-timeline[-1]["start_us"] == 1300000000


def test_rebuild_from_paused_uses_new_revision_and_low_without_invalidating_audio(settings):
    store = highlights.store_for(settings)
    config = {"manifest": {"title": "Recording", "sources": []}, "verification_reasoning": "medium"}
    run = store.admit(config, "paused-rebuild")
    store.update(run, state="paused", progress=0.8, result={"revision": 2, "metrics": {"old": 1},
        "history": [{"revision": 1, "has_draft": True}], "warnings": ["Old failure"], "issues": []})
    highlights.control(store, run, "rebuild", 2)
    current = store.get(run)
    assert current["state"] == "queued" and current["progress"] == 0
    assert current["config"] == config
    assert current["result"]["revision"] == 3 and current["result"]["parent_revision"] is None
    assert current["result"]["editing_reasoning"] == "low"
    assert current["result"]["warnings"] == [] and current["result"]["metrics"] == {}
    assert current["result"]["history"] == [{"revision": 1, "has_draft": True}]


def test_cannot_rebuild_an_active_job(settings):
    store = highlights.store_for(settings)
    run = store.admit({"manifest": {"title": "Recording", "sources": []}}, "running-rebuild")
    store.update(run, state="running", result={"revision": 2})
    with pytest.raises(ValueError):
        highlights.control(store, run, "rebuild", 2)
    assert store.get(run)["state"] == "running"


def test_progress_accounting_is_scoped_to_revision(settings):
    store = highlights.store_for(settings)
    run = store.admit({"manifest": {"title": "Recording", "sources": []}}, "progress")
    old = store.reserve(run, 0, 0, {"step": "scan-0"})
    store.settle(old, 0, {"elapsed_seconds": 100})
    store.update(run, result={"revision": 2, "revision_started": __import__("time").time()})
    current = store.reserve(run, 0, 0, {"step": "episode-edit-repair-b1", "attempt": 1})
    store.settle(current, 0, {"elapsed_seconds": 10})
    value = highlights.public(settings, store, store.get(run))
    assert value["usage"]["request_count"] == 2
    assert value["activity"]["request_count"] == 1
    assert value["activity"]["phases"] == {"Scene editing": {"requests": 1, "retries": 1, "seconds": 10}}


def test_new_highlight_requests_do_not_persist_a_runtime_target():
    request = highlights.Start(manifest_id="a"*32)
    assert "target_minutes" not in request.model_dump()
    legacy = highlights.Start(manifest_id="a"*32, target_minutes=10)
    assert "target_minutes" not in legacy.model_dump()


def test_final_floor_defaults_to_75_and_is_independent_of_discovery():
    rows = items(4)
    pool = [scene("a", "u0", "u1"), scene("b", "u2", "u3")]
    ratings = [{"id": "a", "score": 74}, {"id": "b", "score": 75}]
    selected, decisions = episode.assemble(pool, ratings, rows)
    assert [s["beat"]["id"] for s in selected] == ["b"]
    assert decisions[-1]["decision"] == "below editorial threshold"
    selected, _ = episode.assemble(pool, ratings, rows, floor=70)
    assert len(selected) == 2
    assert highlights.Start(manifest_id="a"*32).final_score_floor == 75


def test_review_evidence_uses_output_clock_and_actual_pause_at_jump_cut():
    rows = items(4)
    evidence = episode.scene_evidence(scene("a", "u0", "u1"), rows, with_context=True, output_start_us=20000000)
    assert evidence[0]["output_start_seconds"] == 20
    assert evidence[1]["output_start_seconds"] == evidence[0]["output_end_seconds"]
    assert evidence[1]["cut_before"] == "jump_cut"
    assert "pause_before=0.80s" in evidence[1]["speech"]
    assert "gap gu0:u1 8.0s" not in json.dumps(evidence)
    assert evidence[0]["reference_only_not_in_video"] == [{"id": "u2", "text": "Thought 2."}, {"id": "u3", "text": "Thought 3."}]
    assert all("Thought 2." not in r["speech"] for r in evidence)


def test_review_uses_continuous_clock_across_scenes_and_episode_index():
    evaluator = Evaluator()
    rows = items(6)
    selected = [scene("a", "u0", "u1"), scene("b", "u4", "u5")]
    episode.critique(selected, rows, evaluator, 0, [])
    payload = evaluator.calls[0][1]
    first, second = payload["assembled"]
    assert second["output_ranges"][0]["output_start_seconds"] == first["output_ranges"][-1]["output_end_seconds"]
    assert "pause_before=0.60s" in second["output_ranges"][0]["speech"]
    assert {s["sequence"] for s in payload["episode_index"]} == {"a", "b"}


def test_minimal_context_addition_preserves_only_requested_passage_and_parent():
    rows = items(8)
    strong = scene("strong", "u3", "u4")
    snapshot = json.dumps(strong)
    addition = episode.ContextAddition(sequence="strong", first="u2", last="u2", reason="Required question")
    expanded = episode.add_context(strong, [addition], rows, [strong])
    assert expanded["edit"]["spans"] == [{"first": "u2", "last": "u4"}]
    assert all(s["first"] not in {"u0", "u1"} for s in episode.compiled(expanded, rows))
    assert json.dumps(strong) == snapshot
    assert expanded["required_context"][0]["reason"] == "Required question"


@pytest.mark.parametrize("fault", ["invented", "not_offered", "overlap", "unsafe"])
def test_context_cannot_invent_anchors_restore_arbitrary_scenes_or_duplicate_footage(fault):
    rows = items(8)
    strong = scene("a", "u3", "u4")
    addition = episode.ContextAddition(sequence="a", first="u2", last="u2", reason="Setup")
    selected = [strong]
    if fault == "invented":
        addition.first = "u999"
    elif fault == "not_offered":
        addition.first = "u0"
    elif fault == "overlap":
        selected.append(scene("b", "u2", "u2"))
    else:
        rows[2]["unsafe"] = True
    with pytest.raises(ModelAnchorError):
        episode.add_context(strong, [addition], rows, selected)


def test_duplicate_review_removes_only_repeated_scene_and_preserves_named_keep(monkeypatch):
    real = Evaluator.call
    def call(self, system, prompt, schema, key, **kwargs):
        if schema is review.Duplicates:
            self.calls.append((key, json.loads(prompt), kwargs))
            result = review.Duplicates(duplicates=[episode.Duplicate(drop="b1", keep="b0", reason="Same point already made")])
            kwargs["validate"](result)
            return result
        return real(self, system, prompt, schema, key, **kwargs)
    monkeypatch.setattr(Evaluator, "call", call)
    evaluator = Evaluator()
    result = episode.plan(items(), evaluator, lambda _: None)
    assert [s["beat"]["id"] for s in result["sequences"]] == ["b0", "b2"]
    assert result["review_history"][0]["duplicate_of"] == "b0"
    assert any(k.startswith("episode-duplicate-check") for k, _, _ in evaluator.calls)


def test_context_request_is_applied_before_rescoring_without_promoting_weak_scene(monkeypatch):
    rows = items(12)
    beats = [scene("b0", "u3", "u4")["beat"], scene("b1", "u8", "u9")["beat"]]
    monkeypatch.setattr(source, "discover", lambda *a: beats)
    real = Evaluator.call
    def call(self, system, prompt, schema, key, **kwargs):
        value = real(self, system, prompt, schema, key, **kwargs)
        if schema is review.Decisions and key.startswith("episode-polish-0"):
            decision = next(c for c in value.scenes if c.sequence == "b0")
            decision.verdict = "change"
            decision.restore = [source.Span(first="u2", last="u2")]
            decision.protect = [source.Span(first="u2", last="u2")]
            kwargs["validate"](value)
        return value
    monkeypatch.setattr(Evaluator, "call", call)
    evaluator = Evaluator()
    result = episode.plan(rows, evaluator, lambda _: None)
    assert result["sequences"][0]["edit"]["spans"] == [{"first": "u2", "last": "u4"}]
    assert len(result["sequences"]) == 2
    assert result["final_score_floor"] == 75
    assert "u2" in result["sequences"][0]["protected_passages"]
    assert len([k for k, _, _ in evaluator.calls if k.startswith("episode-rank")]) == 2


def test_reviewer_schema_requires_all_output_fields():
    schema = episode.EpisodeReview.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"]) == {"issues", "context", "duplicates"}


def test_duplicate_pair_cannot_drop_both_scenes():
    class InvalidReviewer(Evaluator):
        def call(self, system, prompt, schema, key, validate=None, **kwargs):
            result = episode.EpisodeReview(issues=[], context=[], duplicates=[
                episode.Duplicate(drop="a", keep="b", reason="Repeated"),
                episode.Duplicate(drop="b", keep="a", reason="Repeated")])
            validate(result)
    with pytest.raises(ModelAnchorError, match="not dropped"):
        episode.critique([scene("a", "u0", "u1"), scene("b", "u4", "u5")], items(6), InvalidReviewer(), 0, [])
