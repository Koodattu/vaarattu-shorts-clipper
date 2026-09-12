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


def test_comparison_reads_speech_without_prior_editorial_persuasion():
    items = [candidate(i, reason="Persuasive model explanation", title_fi="Amazing title") for i in range(2)]
    words = [dict(text=f"Actual speech {i}", start_us=v["start_us"], end_us=v["end_us"])
             for i, v in enumerate(items)]
    evaluator = Evaluator()
    selection.prioritize({"verified": items}, words, evaluator)
    assert evaluator.supplied == [dict(candidate_id=f"candidate-{i}", speech=f"Actual speech {i}")
                                  for i in range(2)]


def test_normal_twenty_candidate_pool_is_compared_together_once():
    class Compare(Evaluator):
        calls = 0

        def call(self, *args, **kwargs):
            self.calls += 1
            return super().call(*args, **kwargs)

    evaluator = Compare()
    result = selection.prioritize({"verified": [candidate(i) for i in range(20)]}, [], evaluator)
    assert evaluator.calls == result["review_summary"]["comparisons"] == 1
    assert len(evaluator.supplied) == 20
    assert result["review_summary"]["selected"] == 10


def test_comparison_requires_a_complete_permutation():
    class Invalid(Evaluator):
        def call(self, system, prompt, schema, step, validate, reasoning_effort):
            for ids in [["candidate-0"], ["candidate-0", "candidate-0"], ["candidate-0", "invented"]]:
                with pytest.raises(ValueError, match="too_short|exactly once|literal_error"):
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


@pytest.mark.parametrize("count,budget", [(83, 100000), (39, 1700)])
def test_bounded_merging_ranks_the_entire_sparse_pool(count, budget):
    class Bounded(Evaluator):
        discovery_budget = budget
        calls = 0

        def request_size(self, system, prompt, schema):
            return len(prompt.encode()) + 100

        def call(self, system, prompt, schema, step, validate, reasoning_effort):
            rows = json.loads(prompt)
            assert len(rows) <= selection.COMPARISON_SIZE
            assert self.request_size(system, prompt, schema) <= self.discovery_budget
            assert [r["candidate_id"] for r in rows] == [f"candidate-{i}" for i in range(len(rows))]
            self.calls += 1
            result = schema(candidates=[dict(candidate_id=r["candidate_id"], recommendation="review", reason="rank")
                                        for r in sorted(rows, key=lambda r: int(r["speech"]), reverse=True)])
            validate(result)
            return result

    items = [candidate(i, reason=str((i * 17) % count)) for i in range(count)]
    items.insert(2, candidate(count, outcome="reject"))
    evaluator = Bounded()
    words = [dict(text=item["candidate"]["reason"], start_us=item["start_us"], end_us=item["end_us"])
             for item in items if item["candidate"]["outcome"] == "accept"]
    result = selection.prioritize({"verified": items}, words, evaluator)
    ranked = sorted((v for v in result["verified"] if v["review_rank"]), key=lambda v: v["review_rank"])
    assert [int(v["candidate"]["reason"]) for v in ranked] == list(reversed(range(count)))
    assert len(ranked) == count
    assert sum(v["review_selected"] for v in ranked) == 10
    assert result["review_summary"]["method"] == "comparison"
    assert result["review_summary"]["comparisons"] == evaluator.calls > 1


def test_all_suggestions_refresh_top_picks_without_replacing_reviewed_revisions(settings, store, monkeypatch):
    import hashlib
    from vaarattu_shorts.web import public_clip

    run = store.admit({"video": "abc_def-ghI", "layout": {}}, "audit")
    items = [candidate(i) for i in range(13)]
    items[12]["candidate"]["outcome"] = "reject"
    original = candidate(13)["candidate"]
    words = [dict(id=f"{letter}{i}", text="speech", start_us=i*10000000+offset,
                  end_us=i*10000000+offset+500000, probability=1)
             for i in range(14) for letter, offset in [("a", 0), ("b", 3500000)]]
    words[-1]["start_us"], words[-1]["end_us"] = 229500000, 230000000
    supplied = dict(version="conversation-v10", review_first=True, review_policy="ranked-v1",
                    chat={"status": "unavailable"}, verified=items, issues=[],
                    proposals=[v["candidate"] for v in items]+[original])
    ranked = selection.prioritize(supplied, words, Evaluator())
    monkeypatch.setattr(Pipeline, "prioritize_selection", lambda self, s, t: copy.deepcopy(ranked))
    old_ids = []
    for i in range(10):
        clip_id = hashlib.sha256(f"{run}:a{i}:b{i}".encode()).hexdigest()[:32]
        old_ids.append(clip_id)
        folder = settings.ready / clip_id
        folder.mkdir()
        (folder / "short.mp4").write_bytes(b"preview")
        store.save_clip(clip_id, run, 3, dict(status="ready", title="Edited title", words=[],
                                           start_us=i*10000000, end_us=i*10000000+5000000,
                                           folder=str(folder)))
        store.review_clip(clip_id, 3, "approved" if i % 2 else "not_approved", "Keep my note")
    delivered = []
    def deliver(self, clip, *args):
        delivered.append(clip["id"])
        store.save_clip(clip["id"], run, clip["revision"], {**clip["body"], "status": "ready"})
    monkeypatch.setattr(Pipeline, "deliver", deliver)
    pipeline = Pipeline(settings, store, run)
    for _ in range(2):
        result = pipeline.export_selection(supplied, {"words": words, "duration_us": 240000000},
                  {"id": "abc_def-ghI", "title": "VOD", "upload_date": "20260705"}, None, audit=True)
    assert len(store.clips(run)) == 14
    assert len(delivered) == 4
    assert result["review_summary"]["selected"] == 10
    assert result["review_summary"]["audit_all"] is True
    for clip_id in old_ids:
        clip = store.clip(clip_id)
        assert clip["revision"] == 3 and clip["body"]["title"] == "Edited title"
        assert clip["review_status"] != "unreviewed" and clip["review_note"] == "Keep my note"
    clips = sorted([public_clip(c) for c in store.clips(run)],
                   key=lambda c: (c["review_selected"] is False, c["review_rank"] or float("inf")))
    assert all(c["review_selected"] for c in clips[:10])
    assert not store.clip(old_ids[0])["body"]["review_selected"]
    assert sum(bool(c["body"].get("audit_original_bounds")) for c in store.clips(run)) == 1


def test_review_all_refuses_fallback_before_creating_any_clips(settings, store, monkeypatch):
    run = store.admit({"video": "abc_def-ghI", "layout": {}}, "no-fallback")
    ranked = {"verified": [candidate(0), candidate(1)], "review_policy": "ranked-v1",
              "review_summary": {"quality_passed": 2, "method": "scores"}}
    monkeypatch.setattr(Pipeline, "prioritize_selection", lambda self, s, t: ranked)
    with pytest.raises(ValueError, match="Ranking did not complete"):
        Pipeline(settings, store, run).export_selection(ranked, {}, {}, None, audit=True)
    assert store.clips(run) == []


def test_review_all_request_survives_resume_and_worker_dispatch(settings, store, monkeypatch):
    from vaarattu_shorts.worker import run_job
    from vaarattu_shorts.processes import Interrupted

    run = store.admit({}, "dispatch")
    with pytest.raises(ValueError):
        store.control(run, "review-all")
    store.update(run, state="completed")
    store.control(run, "review-all")
    monkeypatch.setattr(Pipeline, "review_all", lambda self: (_ for _ in ()).throw(Interrupted()))
    run_job(settings, store, run, lambda: False)
    assert store.get(run)["state"] == "paused"
    store.control(run, "resume")
    assert store.get(run)["result"]["review_all_requested"] is True


def test_duplicate_id_repair_receives_specific_feedback(settings, store):
    import httpx
    from vaarattu_shorts.llm import Evaluator as RealEvaluator

    run = store.admit({"provider": "codex"}, "repair-ids")
    requests = []
    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        ids = ["candidate-0", "candidate-0"] if len(requests) == 1 else ["candidate-1", "candidate-0"]
        value = dict(candidates=[dict(candidate_id=i, recommendation="review", reason="rank") for i in ids])
        return httpx.Response(200, json={"status": "completed", "output": [{"content": [
            {"type": "output_text", "text": json.dumps(value)}]}],
            "usage": {"input_tokens": 100, "output_tokens": 40}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = RealEvaluator("codex", store, run, settings.work, 0, lambda: None, client)
        result = selection.prioritize({"verified": [candidate(0), candidate(1)]}, [], evaluator)
    assert result["review_summary"]["method"] == "comparison"
    assert len(requests) == 2
    assert "Missing: ['candidate-1']" in requests[1]["input"]
    assert "Repeated: ['candidate-0']" in requests[1]["input"]
    assert "proposed idea inside the clip" not in requests[1]["input"]
    schema = requests[0]["text"]["format"]["schema"]
    assert schema["$defs"]["BatchPriorityItem"]["properties"]["candidate_id"]["enum"] == ["candidate-0", "candidate-1"]


def test_audit_refinement_preserves_long_originals_without_widening_normal_limits():
    from vaarattu_shorts.contracts import clip_duration_limit
    from vaarattu_shorts.transcribe import apply_refinement

    body = dict(start_us=1000000, end_us=110000000)
    transcript = dict(start_us=0, end_us=120000000, timing_issues=[],
                      words=[dict(id="w", text="speech", start_us=1000000, end_us=110000000)])
    with pytest.raises(ValueError, match="crosses"):
        apply_refinement(body, transcript)
    audited = {**body, "audit_original_bounds": dict(body)}
    assert apply_refinement(audited, transcript)["end_us"] == body["end_us"]
    assert clip_duration_limit(body) == 90000000
    assert clip_duration_limit(audited) == 110000000
