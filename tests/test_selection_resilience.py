import json

import httpx
import pytest

from vaarattu_shorts.contracts import Candidate, Proposals, Word
from vaarattu_shorts.discover import discover
from vaarattu_shorts.llm import Evaluator, ModelOutputError
from vaarattu_shorts.pipeline import Pipeline


def words():
    return [
        Word(
            id=f"w{i}",
            start_us=i * 1000000,
            end_us=(i + 1) * 1000000 - 100000,
            text="ajatus." if i % 10 == 9 else "niin",
        ).model_dump()
        for i in range(720)
    ]


def candidate(start=0, **changes):
    return Candidate.model_validate(
        dict(
            outcome="accept",
            start_word_id=f"w{start}",
            end_word_id=f"w{start + 29}",
            idea_word_id=f"w{start}",
            category="opinion",
            summary_fi="Ajatus",
            title_fi="Ajatus",
            scores=dict(standalone=4, opening=4, substance=4, payoff=4, fidelity=4),
            flags=[],
            reason="OK",
        )
        | changes
    )


def response(text):
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 100, "output_tokens": 30},
        },
    )


def test_invalid_proposals_do_not_lose_valid_siblings_or_retry(settings, store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    run = store.admit({}, "siblings")
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return response(
                Proposals(
                    candidates=[candidate(), candidate(400), candidate(end_word_id="invented")]
                ).model_dump_json()
            )
        if len(bodies) == 2:
            return response('{"candidates":[]}')
        return response(candidate().model_dump_json())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("openai", store, run, settings.work, 1, lambda: None, client)
        transcript = {"words": words(), "duration_us": 720000000}
        result = discover(transcript, evaluator, lambda value: None)
        assert discover(transcript, evaluator, lambda value: None) == result
    assert len(bodies) == store.usage(run)["request_count"] == 3
    assert len(result["proposals"]) == 1 and result["verified"][0]["eligible"]
    assert {i["reason"] for i in result["issues"]} == {"outside_section", "invalid_anchors"}
    assert result["coverage"] == [[0, 360000000], [360000000, 720000000]]
    assert json.loads((settings.work / "selection-issues.json").read_text()) == result["issues"]


def test_unreadable_section_gets_one_repair_then_later_sections_continue(settings, store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    run = store.admit({"video": "abc_def-ghI"}, "unreadable")
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        if len(calls) <= 2:
            return response("not JSON")
        if len(calls) == 3:
            return response(Proposals(candidates=[candidate(360)]).model_dump_json())
        return response(candidate(360).model_dump_json())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("openai", store, run, settings.work, 1, lambda: None, client)
        selection = discover({"words": words(), "duration_us": 720000000}, evaluator, lambda value: None)
    assert len(calls) == 4 and store.usage(run)["input_tokens"] == 400
    assert selection["coverage"] == [[360000000, 720000000]]
    assert selection["verified"][0]["eligible"]
    assert selection["issues"][0]["interval"] == [0, 360000000]
    store.save_clip("ready", run, 1, {"status": "ready", "folder": "fixture"})
    result = Pipeline(settings, store, run).finish({"status": "unavailable"}, selection)
    assert result["coverage"] == "partial" and result["outcome"] == "needs_attention"
    assert len(result["ready"]) == 1 and store.get(run)["state"] == "completed"
    assert Pipeline(settings, store, run).finish({"status": "unavailable"})["coverage"] == "partial"
    store.save_video_page(
        {"id": "channel"}, [{"id": "abc_def-ghI", "channel_id": "channel", "published": "today"}]
    )
    assert not store.videos("channel")[0]["processed"]


def test_bad_verification_does_not_stop_other_candidates(settings, store):
    run = store.admit({}, "verification")

    class FixtureEvaluator(Evaluator):
        def call(self, system, prompt, schema, key, **kwargs):
            if schema is Proposals:
                return (
                    Proposals(candidates=[candidate(), candidate(60)])
                    if key.startswith("discovery-0-")
                    else Proposals(candidates=[])
                )
            if key.startswith("verify-0-"):
                raise ModelOutputError("Invalid anchors after repair")
            result = candidate(60)
            kwargs["validate"](result)
            return result

    evaluator = FixtureEvaluator("openai", store, run, settings.work, 1, lambda: None)
    result = discover({"words": words(), "duration_us": 720000000}, evaluator, lambda value: None)
    assert [v["eligible"] for v in result["verified"]] == [False, True]
    assert result["issues"][0]["reason"] == "verification_unreadable"


@pytest.mark.parametrize("failure", ["access", "budget"])
def test_operational_errors_are_not_swallowed(settings, store, monkeypatch, failure):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    run = store.admit({}, "operational")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(403)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator(
            "openai", store, run, settings.work, 0 if failure == "budget" else 1, lambda: None, client
        )
        with pytest.raises(ValueError) as error:
            discover({"words": words(), "duration_us": 720000000}, evaluator, lambda value: None)
    assert not isinstance(error.value, ModelOutputError)
    assert len(calls) == (0 if failure == "budget" else 1)
