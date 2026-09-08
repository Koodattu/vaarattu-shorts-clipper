import copy
import json

import pytest

from vaarattu_shorts import selection
from vaarattu_shorts.llm import ModelOutputError
from vaarattu_shorts.pipeline import Pipeline


def candidate(i, **changes):
    item = {
        "eligible": True,
        "start_us": i * 10000000,
        "end_us": i * 10000000 + 4000000,
        "candidate": {
            "outcome": "accept",
            "start_word_id": f"a{i}",
            "end_word_id": f"b{i}",
            "idea_word_id": f"a{i}",
            "category": "joke",
            "title_fi": f"Title {i}",
            "summary_fi": "Summary",
            "reason": "A recognizable joke",
            "flags": [],
            "scores": dict(standalone=3, opening=3, substance=3, payoff=3, fidelity=3),
        },
    }
    item["candidate"].update(changes)
    return item


class Evaluator:
    discovery_budget = 100000
    verification_reasoning = "medium"

    def request_size(self, *args):
        return 100

    def report(self, message):
        pass

    def call(self, system, prompt, schema, step, validate, reasoning_effort):
        self.supplied = json.loads(prompt)
        assert reasoning_effort == "medium"
        assert "10" not in system
        result = schema(
            candidates=[
                {
                    "candidate_id": row["candidate_id"],
                    "recommendation": "review",
                    "reason": "A joke worth reviewing",
                }
                for row in reversed(self.supplied)
            ]
        )
        validate(result)
        return result


def test_quality_gate_then_comparison_then_application_limit_preserves_every_candidate():
    items = [candidate(i) for i in range(12)]
    items[11]["candidate"]["scores"].update(standalone=2, fidelity=2)
    items.extend([candidate(12, outcome="reject"), candidate(13, outcome="needs_context"), candidate(14)])
    items[14]["candidate"]["scores"]["substance"] = 2
    before = copy.deepcopy(items)
    evaluator = Evaluator()
    result = selection.prioritize({"verified": items}, [], evaluator)
    assert items == before
    assert len(evaluator.supplied) == 12
    selected = [i for i, v in enumerate(result["verified"]) if v["review_selected"]]
    assert selected == list(range(2, 12))
    assert result["verified"][11]["review_rank"] == 1
    assert all(v["review_exclusion_reasons"] for v in result["verified"] if not v["review_selected"])
    assert len(result["verified"]) == 15
    assert result["review_summary"]["selected"] == 10


def test_comparison_defer_and_temporal_duplicates_leave_fewer_than_ten():
    class Compare(Evaluator):
        def call(self, *args, **kwargs):
            value = super().call(*args, **kwargs)
            value.candidates[0].recommendation = "defer"
            return value

    items = [candidate(i) for i in range(4)]
    items[1]["start_us"], items[1]["end_us"] = 1000000, 5000000
    result = selection.prioritize({"verified": items}, [], Compare())
    assert [i for i, v in enumerate(result["verified"]) if v["review_selected"]] == [1, 2]
    assert "same moment" in result["verified"][0]["review_exclusion_reasons"][0]
    assert result["verified"][3]["priority_reason"]


@pytest.mark.parametrize("too_large", [True, False])
def test_unavailable_comparison_uses_scores_with_visible_warning(too_large):
    class Unavailable(Evaluator):
        discovery_budget = 0 if too_large else 100000

        def call(self, *args, **kwargs):
            raise ModelOutputError("Invalid ranking")

    result = selection.prioritize({"verified": [candidate(i) for i in range(11)]}, [], Unavailable())
    assert result["review_summary"]["method"] == "scores"
    assert result["review_summary"]["warning"]
    assert sum(v["review_selected"] for v in result["verified"]) == 10


def test_one_candidate_needs_no_comparison_and_no_eligible_candidates_are_filled():
    result = selection.prioritize({"verified": [candidate(0)]}, [], None)
    assert result["review_summary"]["selected"] == 1
    result = selection.prioritize({"verified": [candidate(0, outcome="reject")]}, [], None)
    assert result["review_summary"]["selected"] == 0


def test_comparison_requires_a_complete_permutation():
    class Invalid(Evaluator):
        def call(self, system, prompt, schema, step, validate, reasoning_effort):
            for ids in [["candidate-0"], ["candidate-0", "candidate-0"], ["candidate-0", "invented"]]:
                with pytest.raises(ValueError, match="exactly once"):
                    validate(
                        schema(
                            candidates=[
                                {"candidate_id": i, "recommendation": "review", "reason": ""} for i in ids
                            ]
                        )
                    )
            return super().call(system, prompt, schema, step, validate, reasoning_effort)

    selection.prioritize({"verified": [candidate(0), candidate(1)]}, [], Invalid())


def test_pipeline_renders_only_ranked_ten_and_resume_preserves_exports(settings, store, monkeypatch):
    run = store.admit({"video": "abc_def-ghI", "layout": {}}, "ranked")
    items = [candidate(i) for i in range(12)]
    ranked = selection.prioritize(
        {
            "version": "conversation-v10",
            "review_first": True,
            "review_policy": "ranked-v1",
            "chat": {"status": "unavailable"},
            "verified": items,
            "issues": [],
        },
        [],
        Evaluator(),
    )
    monkeypatch.setattr(Pipeline, "prioritize_selection", lambda self, s, t: copy.deepcopy(ranked))
    delivered = []

    def deliver(self, clip, source, duration):
        delivered.append(clip["id"])
        self.store.save_clip(clip["id"], run, clip["revision"], {**clip["body"], "status": "ready"})

    monkeypatch.setattr(Pipeline, "deliver", deliver)
    pipeline = Pipeline(settings, store, run)
    metadata = {"id": "abc_def-ghI", "title": "VOD", "upload_date": "20260705"}
    for _ in range(2):
        result = pipeline.export_selection(ranked, {"words": [], "duration_us": 120000000}, metadata, None)
    assert len(delivered) == len(store.clips(run)) == 10
    assert [c["body"]["review_rank"] for c in store.clips(run)] == list(range(1, 11))
    assert len(result["verified"]) == 12
    assert result["review_summary"]["selected"] == 10


def test_priority_checkpoint_reuses_saved_model_comparison(settings, store, monkeypatch):
    import vaarattu_shorts.pipeline as pipeline_module

    calls = []

    def evaluator(*args, **kwargs):
        calls.append(True)
        return Evaluator()

    monkeypatch.setattr(pipeline_module, "Evaluator", evaluator)
    run = store.admit({"provider": "openai", "budget_usd": 1}, "cache")
    supplied = {"verified": [candidate(0), candidate(1)]}
    first = Pipeline(settings, store, run).prioritize_selection(supplied, {"words": []})
    assert Pipeline(settings, store, run).prioritize_selection(supplied, {"words": []}) == first
    assert len(calls) == 1


@pytest.mark.parametrize("existing_count", [2, 12])
def test_recovery_capacity_counts_existing_exports_without_deleting_them(
    settings, store, monkeypatch, existing_count
):
    run = store.admit({"video": "abc_def-ghI", "layout": {}}, "existing")
    old = {"status": "ready", "start_us": 200000000, "end_us": 210000000}
    for i in range(existing_count):
        store.save_clip(f"old-{i}", run, 3, old)
    supplied = {
        "version": "conversation-v10",
        "review_first": True,
        "review_policy": "ranked-v1",
        "chat": {"status": "unavailable"},
        "verified": [candidate(i) for i in range(12)],
        "issues": [],
    }
    ranked = selection.prioritize(supplied, [], Evaluator())
    monkeypatch.setattr(Pipeline, "prioritize_selection", lambda self, s, t: ranked)
    monkeypatch.setattr(
        Pipeline,
        "deliver",
        lambda self, clip, *args: store.save_clip(clip["id"], run, 1, {**clip["body"], "status": "ready"}),
    )
    result = Pipeline(settings, store, run).export_selection(
        supplied,
        {"words": [], "duration_us": 300000000},
        {"id": "abc_def-ghI", "title": "VOD", "upload_date": "20260705"},
        None,
    )
    assert len(store.clips(run)) == max(10, existing_count)
    assert result["review_summary"]["selected"] == max(0, 10 - existing_count)
    for i in range(existing_count):
        assert store.clip(f"old-{i}")["body"] == old
        assert store.clip(f"old-{i}")["revision"] == 3
