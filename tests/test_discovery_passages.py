import json

import httpx
import pytest

from vaarattu_shorts.contracts import Candidate, Proposals, Word
from vaarattu_shorts.discover import (
    CONTEXT_US,
    SYSTEM,
    anchors,
    discover,
    discovery_prompt,
    lines,
    passages,
    validate_anchors,
    windows,
)
from vaarattu_shorts.llm import Evaluator
from vaarattu_shorts.pipeline import Pipeline


def speech(count, interval=1000000):
    return [
        Word(
            id=f"w{i}",
            start_us=i * interval,
            end_us=(i + 1) * interval - 100000,
            text="ajatus." if i % 10 == 9 else "niin",
        )
        for i in range(count)
    ]


def candidate(**changes):
    body = dict(
        outcome="accept",
        start_word_id="w0",
        end_word_id="w29",
        idea_word_id="w0",
        category="opinion",
        summary_fi="Arvio",
        title_fi="Ajatus",
        flags=[],
        reason="discovery-bias",
        scores=dict(standalone=4, opening=4, substance=4, payoff=4, fidelity=4),
    )
    return Candidate.model_validate({**body, **changes})


def test_passages_preserve_every_original_word_and_pause():
    words = speech(60)
    words[0].text, words[1].text, words[2].text = "En", "esim.", "väitä"
    original = [w.model_dump() for w in words]
    groups = passages(words)
    assert [w for g in groups for w in g] == words
    assert [w.model_dump() for w in words] == original
    assert groups[0][1].text == "esim." and len(groups[0]) == 10
    assert "En esim. väitä niin niin" in lines(words)
    assert "[w1]" not in lines(words)
    words[30:] = [
        w.model_copy(update={"start_us": w.start_us + 5000000, "end_us": w.end_us + 5000000})
        for w in words[30:]
    ]
    assert "[pause 5.1s]" in lines(words)
    assert sum(map(len, passages(words))) == 60


def test_unpunctuated_speech_is_bounded_without_rewriting():
    words = [w.model_copy(update={"text": "ääkkönen"}) for w in speech(100, 200000)]
    groups = passages(words)
    assert [w.id for g in groups for w in g] == [w.id for w in words]
    assert all(len(g) <= 45 and g[-1].end_us - g[0].start_us <= 15000000 for g in groups)


def test_word_detail_is_limited_to_shortlisted_area_and_anchors_are_real():
    words = speech(100)
    detail = (20000000, 40000000)
    text = lines(words, detail)
    assert "[w21]" in text and "[w81]" not in text
    start, end = anchors(words)
    assert "w20" in start and "w29" in end
    with pytest.raises(ValueError, match="not shown"):
        validate_anchors(candidate(start_word_id="w1", idea_word_id="w10"), words)
    validate_anchors(candidate(start_word_id="w21", end_word_id="w38", idea_word_id="w25"), words, detail)
    with pytest.raises(ValueError, match="outside"):
        validate_anchors(candidate(end_word_id="invented"), words)


def test_six_hour_dense_finnish_stays_in_six_minute_api_windows(tmp_path):
    words = speech(6 * 3600 * 2, 500000)
    words = [
        w.model_copy(update={"text": "mielestäni." if i % 20 == 19 else "suomalaisille"})
        for i, w in enumerate(words)
    ]
    evaluator = Evaluator("openai", None, "offline", tmp_path, 1, lambda: None)
    planned = list(windows(words, 21600000000, evaluator))
    assert len(planned) == 60
    assert set(w.id for _, _, c in planned for w in c) == set(w.id for w in words)
    assert all(
        evaluator.request_size(SYSTEM, discovery_prompt(c, a, b), Proposals) <= evaluator.discovery_budget
        for a, b, c in planned
    )
    assert (
        evaluator.discovery_budget
        == Evaluator("openai", None, "x", tmp_path, 1, lambda: None, context_size=32768).discovery_budget
    )


def test_budget_splits_ownership_without_shrinking_context(tmp_path, monkeypatch):
    words = speech(720)
    evaluator = Evaluator("openai", None, "offline", tmp_path, 1, lambda: None)
    evaluator.discovery_budget = 2500
    monkeypatch.setattr(evaluator, "request_size", lambda system, prompt, schema: len(prompt))
    planned = list(windows(words, 720000000, evaluator))
    assert len(planned) > 2
    assert planned[0][0] == 0 and planned[-1][1] == 720000000
    assert all(left[1] == right[0] for left, right in zip(planned, planned[1:]))
    groups = passages(words)
    for start, end, context in planned:
        expected = [
            w.id
            for g in groups
            if g[-1].end_us > start - CONTEXT_US and g[0].start_us < end + CONTEXT_US
            for w in g
        ]
        assert [w.id for w in context] == expected
    owned = [g[0].id for a, b, _ in planned for g in groups if a <= g[0].start_us < b]
    assert owned == [g[0].id for g in groups]


def test_context_that_cannot_fit_fails_instead_of_losing_speech(tmp_path):
    evaluator = Evaluator("openai", None, "offline", tmp_path, 1, lambda: None)
    evaluator.discovery_budget = 1
    with pytest.raises(ValueError, match="surrounding context"):
        list(windows(speech(1), 1000000, evaluator))


@pytest.mark.parametrize(
    "outcome,flags,eligible",
    [
        ("accept", [], True),
        ("reject", [], False),
        ("accept", ["speaker uncertain"], False),
        ("needs_context", [], False),
    ],
)
def test_two_pass_selection_is_blind_precise_and_reports_progress(tmp_path, outcome, flags, eligible):
    calls, reports = [], []

    class FixtureEvaluator(Evaluator):
        def report(self, message):
            reports.append(message)

        def call(self, system, prompt, schema, key, *, validate=None, reasoning_effort="none"):
            calls.append((prompt, reasoning_effort))
            result = (
                Proposals(candidates=[candidate()])
                if schema is Proposals
                else candidate(outcome=outcome, flags=flags)
            )
            if validate:
                validate(result)
            return result

    evaluator = FixtureEvaluator("openai", None, "offline", tmp_path, 1, lambda: None)
    words = speech(60)
    result = discover(
        {"words": [w.model_dump() for w in words], "duration_us": 60000000}, evaluator, lambda value: None
    )
    assert len(calls) == 2 and [r for _, r in calls] == ["low", "low"]
    assert "discovery-bias" not in calls[1][0] and '"standalone":4' not in calls[1][0]
    assert "[w1]" not in calls[0][0] and "[w1]" in calls[1][0]
    assert result["verified"][0]["eligible"] is eligible
    assert result["coverage"] == [[0, 60000000]]
    assert "1 of 1" in reports[0]
    assert json.loads((tmp_path / "discovery-plan.json").read_text())["discovery_requests"] == 1


def test_semantic_repair_cache_identity_and_native_schema(settings, store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    run = store.admit({"budget_usd": 1}, "semantic")
    bodies = []

    def handle(request):
        body = json.loads(request.content)
        bodies.append(body)
        value = candidate(end_word_id="unknown" if len(bodies) == 1 else "w29")
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": value.model_dump_json()}]}],
                "usage": {"input_tokens": 100, "output_tokens": 30},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("openai", store, run, settings.work, 1, lambda: None, client)

        def validate(c):
            validate_anchors(c, speech(60))

        for _ in range(2):
            evaluator.call(
                "rules", "speech", Candidate, "verify-0", validate=validate, reasoning_effort="low"
            )
        assert len(bodies) == 2
        evaluator.call(
            "changed rules", "speech", Candidate, "verify-0", validate=validate, reasoning_effort="low"
        )
        assert len(bodies) == 3
    assert all(b["instructions"] in {"rules", "changed rules"} for b in bodies)
    assert all(b["text"]["format"]["type"] == "json_schema" for b in bodies)
    assert all(b["reasoning"]["effort"] == "low" for b in bodies)
    assert len(list(settings.work.glob("*.response.json"))) == 3
    usage = store.usage(run)
    assert usage["request_count"] == 3 and usage["input_tokens"] == 300
    assert all(r["reasoning_effort"] == "low" for r in usage["requests"])
    assert usage["requests"][0]["request_sha256"] != usage["requests"][1]["request_sha256"]
    assert usage["requests"][0]["prompt_sha256"] != usage["requests"][-1]["prompt_sha256"]


def test_selection_version_invalidates_only_selection_and_later(settings, store):
    run = store.admit({}, "versions")
    counts = {"transcript": 0, "selection": 0}

    def operation(name):
        counts[name] += 1
        return {}, []

    for version in ("old", "new", "new"):
        pipeline = Pipeline(settings, store, run)
        pipeline.stage("transcript", lambda: operation("transcript"))
        pipeline.stage("selection", lambda: operation("selection"), version)
    assert counts == {"transcript": 1, "selection": 2}
