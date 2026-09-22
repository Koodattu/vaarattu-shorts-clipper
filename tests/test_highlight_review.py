"""Regression cases for grounded corrections, bounded review and independent requests."""
import copy
import json
from threading import Barrier, Lock

import pytest

from vaarattu_shorts import highlight_edit as source, highlight_episode as episode, highlight_review as review
from vaarattu_shorts.llm import ModelAnchorError, ModelOutputError
from test_highlight_episode import Evaluator, items, scene


def decision(ident="a", **changes):
    return review.Decision.model_validate({"sequence": ident, "verdict": "change", "remove": [], "restore": [], "pauses": [], "protect": [],
        "reason": "Remove only redundant speech; retain setup and payoff.", **changes})


def test_patch_removes_whole_passages_preserves_protected_setup_and_parent():
    rows = items(6)
    old = scene("a", "u0", "u4")
    saved = copy.deepcopy(old)
    result = review.patch(old, decision(remove=[{"first": "u2", "last": "u2"}],
                          protect=[{"first": "u0", "last": "u1"}]), rows, [old])
    assert result["edit"]["spans"] == [{"first": "u0", "last": "u1"}, {"first": "u3", "last": "u4"}]
    assert result["protected_passages"] == ["u0", "u1"]
    assert old == saved
    with pytest.raises(ModelAnchorError, match="protected"):
        review.patch(result, decision(remove=[{"first": "u0", "last": "u0"}]), rows, [result])


@pytest.mark.parametrize("fault", ["reference_removal", "invented", "protected", "unoffered_context", "removed_gap"])
def test_invalid_patch_leaves_the_previous_scene_unchanged(fault):
    rows = items(8)
    old = scene("a", "u2", "u5")
    saved = copy.deepcopy(old)
    changes = {
        "reference_removal": {"remove": [{"first": "u1", "last": "u1"}]},
        "invented": {"remove": [{"first": "unknown", "last": "unknown"}]},
        "protected": {"remove": [{"first": "u2", "last": "u2"}], "protect": [{"first": "u2", "last": "u2"}]},
        "unoffered_context": {"restore": [{"first": "u0", "last": "u3"}]},
        "removed_gap": {"remove": [{"first": "u3", "last": "u3"}], "pauses": [
            {"id": "gu2:u3", "action": "shorten", "evidence": "u2", "seconds": 0, "reason": "Waiting"}]},
    }
    with pytest.raises(ModelAnchorError):
        review.patch(old, decision(**changes[fault]), rows, [old])
    assert old == saved


def test_explicit_pause_correction_shortens_wait_without_cutting_speech():
    rows = items(3)
    old = scene("a", "u0", "u2")
    old["edit"]["gaps"] = [{"id": "gu0:u1", "action": "keep", "evidence": "u1", "seconds": 0, "reason": "Anticipation"}]
    result = review.patch(old, decision(pauses=[{"id": "gu0:u1", "action": "shorten", "evidence": "u1", "seconds": 0, "reason": "No supported event"}]), rows, [old])
    assert episode.seconds(episode.compiled(result, rows)) < episode.seconds(episode.compiled(old, rows))
    for u in rows:
        assert any(s["start_us"] <= u["speech_start_us"] < u["speech_end_us"] <= s["end_us"] for s in episode.compiled(result, rows))


class Reviewer(Evaluator):
    def __init__(self, replies):
        super().__init__()
        self.replies = iter(replies)

    def call(self, system, prompt, schema, key, validate=None, **kw):
        if schema in {review.Decisions, review.Checks}:
            payload = json.loads(prompt)
            self.calls.append((key, payload, kw))
            result = schema.model_validate({"scenes": [next(self.replies)]})
            validate(result)
            return result
        return super().call(system, prompt, schema, key, validate=validate, **kw)


def test_second_review_correction_is_applied_and_then_verified(monkeypatch):
    rows = items(5)
    pool = [scene("a", "u0", "u4")]
    evaluator = Reviewer([decision(remove=[{"first": "u1", "last": "u1"}]).model_dump(),
                          decision(remove=[{"first": "u3", "last": "u3"}]).model_dump(),
                          {"sequence": "a", "verdict": "resolved", "reason": "Both repetitions removed; story intact."}])
    monkeypatch.setattr(episode, "rank_scenes", lambda *a: [{"id": "a", "score": 80}])
    selected, _, _, ledger, issues = review.finish(pool, [{"id": "a", "score": 80}], rows, evaluator, 75, [], lambda _: None)
    assert selected[0]["edit"]["spans"] == [{"first": "u0", "last": "u0"}, {"first": "u2", "last": "u2"}, {"first": "u4", "last": "u4"}]
    assert [r["status"] for r in ledger] == ["applied", "applied", "verified"]
    assert not issues
    assert [k for k, _, _ in evaluator.calls] == ["episode-polish-0-0", "episode-polish-1-0", "episode-verify-2-0"]


def test_reversal_stops_instead_of_repeatedly_deleting_and_restoring_setup(monkeypatch):
    rows = items(5)
    pool = [scene("a", "u0", "u4")]
    evaluator = Reviewer([decision(remove=[{"first": "u1", "last": "u1"}]).model_dump(),
                          decision(restore=[{"first": "u1", "last": "u1"}]).model_dump()])
    monkeypatch.setattr(episode, "rank_scenes", lambda *a: [{"id": "a", "score": 80}])
    selected, _, _, ledger, issues = review.finish(pool, [{"id": "a", "score": 80}], rows, evaluator, 75, [], lambda _: None)
    assert ledger[-1]["status"] == "declined" and "reverses" in issues[0]["instruction"]
    assert "u1" not in review.passage_ids(selected[0]["edit"]["spans"], rows)
    assert len(evaluator.calls) == 2


def test_missing_scene_verdict_is_not_silently_accepted():
    class Missing(Evaluator):
        def call(self, system, prompt, schema, key, validate=None, **kw):
            value = review.Decisions(scenes=[])
            validate(value)
    with pytest.raises(ModelAnchorError, match="every supplied scene"):
        review.review([scene("a", "u0", "u1")], items(2), Missing(), {"a"}, [], "review")


def test_failed_review_is_explicitly_unresolved():
    class Broken(Evaluator):
        def call(self, *a, **kw):
            raise ModelOutputError("Invalid response")
    results = review.review([scene("a", "u0", "u1")], items(2), Broken(), {"a"}, [], "review")
    assert results[0].verdict == "unresolved"


def test_local_review_omits_global_index_but_includes_neighbors():
    evaluator = Evaluator()
    review.review([scene("a", "u0", "u1"), scene("b", "u3", "u4")], items(5), evaluator, {"a"}, [], "review")
    payload = evaluator.calls[0][1]
    assert "episode_index" not in payload
    assert [s["sequence"] for s in payload["scenes"]] == ["a"]
    assert payload["scenes"][0]["neighbors"][0]["sequence"] == "b"


def test_parallel_requests_are_bounded_and_results_remain_in_source_order():
    evaluator = Evaluator()
    evaluator.provider = "codex"
    barrier, lock = Barrier(2), Lock()
    active = peak = 0
    def run(value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1
        return value
    assert list(source.parallel_requests(evaluator, run, list(range(6)))) == list(range(6))
    assert peak == 2


def test_local_model_calls_stay_serial():
    evaluator = Evaluator()
    evaluator.provider = "local"
    calls = []
    result = source.parallel_requests(evaluator, lambda x: calls.append(x) or x, [1, 2])
    assert next(result) == 1 and calls == [1]
    assert next(result) == 2 and calls == [1, 2]


def test_bad_scene_decision_repairs_only_that_scene_and_preserves_neighbor():
    class PartlyBroken(Evaluator):
        def call(self, system, prompt, schema, key, validate=None, **kw):
            payload = json.loads(prompt)
            self.calls.append((key, payload, kw))
            if len(self.calls) == 1:
                values = [decision("a", verdict="acceptable", remove=[{"first": "u0", "last": "u0"}]),
                          decision("b", remove=[{"first": "u3", "last": "u3"}])]
            else:
                values = [decision("a", verdict="acceptable")]
            result = review.Decisions(scenes=values)
            validate(result)
            return result
    evaluator = PartlyBroken()
    result = review.review([scene("a", "u0", "u1"), scene("b", "u3", "u4")], items(5), evaluator, {"a", "b"}, [], "review")
    assert [(d.sequence, d.verdict) for d in result] == [("a", "acceptable"), ("b", "change")]
    assert result[1].remove == [source.Span(first="u3", last="u3")]
    assert len(evaluator.calls) == 2
    assert [c["sequence"] for c in evaluator.calls[1][1]["scenes"]] == ["a"]


def test_verification_receives_measured_pause_change_with_speech_margins():
    rows = items(2)
    rows[1].update(start_us=15700000, speech_start_us=16000000, speech_end_us=18000000, end_us=18300000)
    old = scene("a", "u0", "u1")
    old["edit"]["gaps"] = [{"id": "gu0:u1", "action": "keep", "evidence": "u1", "seconds": 0, "reason": "Wait"}]
    change = decision(pauses=[{"id": "gu0:u1", "action": "shorten", "evidence": "u1", "seconds": 0, "reason": "Remove wait"}])
    updated = review.patch(old, change, rows, [old])
    measured = review.measured_change(old, updated, rows, change)
    assert measured["pauses"] == [{"id": "gu0:u1", "before_seconds": 13, "after_seconds": 0.8}]
    assert measured["removed_passages"] == []
    assert measured["before_seconds"]-measured["after_seconds"] == pytest.approx(12.2)
    evaluator = Reviewer([{"sequence": "a", "verdict": "resolved", "reason": "The wait is removed."}])
    review.review([updated], rows, evaluator, {"a"}, [{"sequence": "a", "status": "applied", "before_scene": old, "measured_change": measured}], "check", verify=True)
    previous = evaluator.calls[0][1]["scenes"][0]["previous_decisions"][0]
    assert previous["measured_change"] == measured
    assert "before_scene" not in previous


def test_duplicate_returns_if_keeper_falls_below_floor_after_correction(monkeypatch):
    rows = items(5)
    pool = [scene("a", "u0", "u1"), scene("b", "u3", "u4")]
    monkeypatch.setattr(review, "duplicates", lambda *a: [episode.Duplicate(drop="b", keep="a", reason="Same point")])
    monkeypatch.setattr(episode, "rank_scenes", lambda *a: [{"id": "a", "score": 60}])
    evaluator = Reviewer([decision(remove=[{"first": "u0", "last": "u0"}]).model_dump(),
                          decision("b", verdict="acceptable").model_dump()])
    selected, _, _, _, issues = review.finish(pool, [{"id": "a", "score": 90}, {"id": "b", "score": 80}], rows, evaluator, 75, [], lambda _: None)
    assert [s["beat"]["id"] for s in selected] == ["b"]
    assert "excluded_as_duplicate_of" not in selected[0]
    assert not issues


def test_summary_keeps_unreviewed_and_unresolved_distinct():
    plan = {"version": "episode-v5", "sequences": [scene(i, "u0", "u1") for i in ["a", "b", "c"]],
            "review_history": [{"sequence": "a", "status": "applied"}, {"sequence": "a", "status": "verified"},
                               {"sequence": "b", "status": "declined"}, {"sequence": "excluded", "status": "unresolved"}]}
    assert review.summary(plan) == {"verified": 1, "unresolved": 1, "not_reviewed": 1, "corrections": 1}
    assert review.summary({"version": "episode-v4"}) is None
