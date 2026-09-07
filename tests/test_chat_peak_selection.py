import httpx
import pytest

from vaarattu_shorts import discover, stream_data
from vaarattu_shorts.contracts import Candidate, Proposals, Word
from vaarattu_shorts.llm import Evaluator, ModelOutputError


def activity():
    return {
        "status": "aligned",
        "coverage": "unknown",
        "points": [
            {
                "start_us": i * 60000000,
                "end_us": (i + 1) * 60000000,
                "active_chatters": 30 if i in (8, 9) else 10,
            }
            for i in range(20)
        ],
    }


def speech(count=1200):
    return {
        "duration_us": count * 1000000,
        "words": [
            Word(
                id=f"w{i}",
                start_us=i * 1000000,
                end_us=(i + 1) * 1000000,
                text="ajatus." if i % 10 == 9 else "niin",
            ).model_dump()
            for i in range(count)
        ],
    }


def candidate(start=420):
    return Candidate(
        outcome="accept",
        start_word_id=f"w{start}",
        end_word_id=f"w{start + 29}",
        idea_word_id=f"w{start}",
        category="story",
        title_fi="Tarina",
        summary_fi="Tarina",
        reason="Konkreettinen alku ja lopputulos",
        flags=[],
        scores=dict(standalone=4, opening=4, substance=4, payoff=4, fidelity=4),
    )


def test_peak_detection_merges_reaction_regions_and_preserves_unknown_coverage():
    data = activity()
    regions = stream_data.peak_regions(data, 1200000000)
    assert len(regions) == 1
    assert regions[0]["start_us"] == 390000000 and regions[0]["end_us"] == 630000000
    assert len(regions[0]["peaks"]) == 2 and regions[0]["peaks"][0]["baseline"] == 10
    assert data["coverage"] == "unknown"
    assert not stream_data.peak_regions({**data, "status": "timing_unconfirmed"}, 1200000000)
    for p in data["points"]:
        p["active_chatters"] = 0
    data["points"][8]["active_chatters"] = 50
    assert not stream_data.peak_regions(data, 1200000000), "Missing/zero data cannot create a quiet baseline"


@pytest.mark.parametrize("peak_error", [None, "unreadable", "quota"])
def test_peak_pass_adds_candidates_keeps_full_scan_and_verifies_without_activity_bias(settings, peak_error):
    calls = []

    class FakeEvaluator:
        folder = settings.work
        discovery_budget = verification_budget = 48000
        discovery_reasoning = verification_reasoning = "low"

        def check(self):
            pass

        def report(self, message):
            pass

        def request_size(self, *args):
            return 100

        def call(self, system, prompt, schema, key, *, validate=None, reasoning_effort=None):
            calls.append((key, prompt, reasoning_effort))
            if key.startswith("discovery-"):
                result = Proposals(
                    feedback="Fixture section feedback.",
                    candidates=[candidate()] if key.startswith("discovery-360000000-") else [],
                )
            elif key.startswith("chat-peak-"):
                assert "480.0..540.0, 540.0..600.0" in prompt
                if peak_error == "quota":
                    raise ValueError("quota unavailable")
                if peak_error == "unreadable":
                    raise ModelOutputError("fixture")
                result = Proposals(
                    feedback="Fixture section feedback.", candidates=[candidate(), candidate(550)]
                )
            else:
                assert "active chatters" not in prompt and "SECOND DISCOVERY" not in prompt
                assert "Activity bucket intervals" not in prompt
                result = candidate(550 if "Proposed start=w550," in prompt else 420)
            if validate:
                validate(result)
            return result

    if peak_error == "quota":
        with pytest.raises(ValueError, match="quota"):
            discover.discover(speech(), FakeEvaluator(), lambda _: None, activity())
        return
    result = discover.discover(speech(), FakeEvaluator(), lambda _: None, activity())
    assert result["coverage"] == [
        [0, 360000000],
        [360000000, 720000000],
        [720000000, 1080000000],
        [1080000000, 1200000000],
    ]
    assert all(key.startswith("discovery-") for key, _, _ in calls[:4])
    assert calls[4][0].startswith("chat-peak-") and all(c[2] == "low" for c in calls)
    if peak_error:
        assert result["chat_peak_review"]["status"] == "partial"
        assert result["issues"][0]["reason"] == "chat_peak_unreadable"
        assert len(result["verified"]) == 1 and result["verified"][0]["eligible"]
    else:
        assert len(result["verified"]) == 2 and all(v["eligible"] for v in result["verified"])
        assert result["verified"][0]["discovery_sources"] == ["chat_peak", "transcript"]
        assert result["verified"][1]["discovery_sources"] == ["chat_peak"]
        assert result["chat_peak_review"]["checked"] == 1


@pytest.mark.parametrize(
    "scores,has_review_note",
    [
        ({"opening": 0}, False),
        ({"payoff": 0}, False),
        ({"opening": 0, "payoff": 0, "standalone": 3, "substance": 3, "fidelity": 3}, False),
        ({"substance": 2}, True),
        ({"standalone": 2}, True),
        ({"fidelity": 2}, True),
    ],
)
def test_scores_are_preserved_as_review_notes_without_editorial_vetoes(settings, scores, has_review_note):
    class FakeEvaluator:
        folder = settings.work
        discovery_budget = verification_budget = 48000
        discovery_reasoning = verification_reasoning = "low"

        def check(self):
            pass

        def report(self, message):
            pass

        def request_size(self, *args):
            return 100

        def call(self, system, prompt, schema, key, **kwargs):
            result = candidate(0)
            if schema is Proposals:
                return Proposals(feedback="Fixture section feedback.", candidates=[result])
            for field, score in scores.items():
                setattr(result.scores, field, score)
            return result

    result = discover.discover(speech(60), FakeEvaluator(), lambda _: None)
    assert result["verified"][0]["eligible"]
    assert result["verified"][0]["exclusion_reasons"] == []
    assert bool(result["verified"][0]["review_notes"]) == has_review_note
    for field, score in scores.items():
        assert result["verified"][0]["candidate"]["scores"][field] == score


@pytest.mark.parametrize("trim_start,eligible", [(3, True), (15, True), (40, False)])
def test_refinement_can_move_within_proposal_but_not_outside_it(
    settings, store, monkeypatch, trim_start, eligible
):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    run = store.admit({}, "trim")
    calls = []

    def handle(request):
        calls.append(request)
        refined = candidate(0).model_copy(
            update={
                "start_word_id": f"w{trim_start}",
                "idea_word_id": f"w{trim_start}",
                "end_word_id": "w49" if trim_start == 40 else "w29",
            }
        )
        value = (
            Proposals(feedback="Fixture section feedback.", candidates=[candidate(0)])
            if len(calls) == 1
            else refined
        )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": value.model_dump_json()}]}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("openai", store, run, settings.work, 1, lambda: None, client)
        result = discover.discover(speech(60), evaluator, lambda _: None)
    assert result["verified"][0]["eligible"]
    if not eligible:
        assert result["issues"][0]["reason"] == "verification_invalid_anchors"
        assert "outside the proposed excerpt" in result["issues"][0]["detail"]
        assert result["verified"][0]["start_us"] == 0
        assert result["verified"][0]["end_us"] == 30000000
        assert result["verified"][0]["candidate"]["outcome"] == "needs_context"
        assert result["verified"][0]["review_notes"]
    if eligible:
        assert result["verified"][0]["start_us"] == trim_start * 1000000


@pytest.mark.parametrize("confirmed", [False, True])
def test_live_contract_mapping_needs_explicit_confirmation(monkeypatch, confirmed):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/activity"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "intervalMinutes": 2,
                        "points": [
                            {
                                "time": "2026-07-09T14:53:00Z",
                                "endTime": "2026-07-09T14:55:00Z",
                                "activeChatters": 15,
                            }
                        ],
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [
                    {"id": 254, "startTime": "2026-07-09T14:51:00Z", "segments": [{"title": "Keskustelu"}]}
                ],
            },
        )

    factory = httpx.Client
    monkeypatch.setattr(
        stream_data.httpx, "Client", lambda **kwargs: factory(transport=httpx.MockTransport(handle), **kwargs)
    )
    result = stream_data.enrich(
        {"title": "9.7.2026 - Keskustelu"},
        {"alignment_confirmed": confirmed, "stream_id": 254, "stream_offset_seconds": 30},
    )
    if confirmed:
        assert result["status"] == "aligned"
        assert result["points"][0]["start_us"] == 90000000
        assert result["points"][0]["end_us"] == 210000000
        assert result["coverage"] == "unknown"
    else:
        assert result["status"] == "timing_unconfirmed" and len(calls) == 1
