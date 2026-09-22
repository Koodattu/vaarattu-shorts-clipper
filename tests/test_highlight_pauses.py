"""Pause repairs preserve speech, constrain references, and have a safe bounded fallback."""
import copy
import json
from threading import Event, Lock

import pytest
from pydantic import ValidationError

from vaarattu_shorts import highlight_episode as episode, highlight_pauses as pauses, highlight_review as review
from vaarattu_shorts.llm import ModelAnchorError, ModelOutputError
from vaarattu_shorts.processes import Interrupted
from test_highlight_episode import Evaluator, items, scene


class Repairer(Evaluator):
    def __init__(self, response=None, failure=None):
        super().__init__()
        self.response, self.failure = response, failure

    def call(self, system, prompt, schema, key, validate=None, **kwargs):
        self.calls.append((key, json.loads(prompt), kwargs))
        if self.failure:
            raise self.failure
        value = schema.model_validate(self.response)
        validate(value)
        return value


def proposal(**changes):
    return episode.Proposal.model_validate({"spans": [{"first": "u0", "last": "u3"}],
        "pauses": [{"id": "gu0:u1", "action": "lead_in", "seconds": 3, "reason": "Reaction"},
                   {"id": "gu1:u2", "action": "keep", "evidence": "Quoted speech", "reason": "Comic timing"}],
        "value": 3, "reason": "Keep the exchange", **changes})


def test_short_refs_are_scene_scoped_and_schema_rejects_quotes_and_unknown_gaps():
    rows = items(4)
    beat = scene("a", "u0", "u3")["beat"]
    entry = pauses.card(beat, rows)
    assert "gap g1 8.0s" in entry["speech"] and "gu0:u1" not in entry["speech"]
    schema = pauses.schema([entry], {"a": rows})
    data = {"scenes": [{"id": "a", "spans": [{"first": "u0", "last": "u3"}],
             "pauses": [{"gap_ref": "g2", "action": "keep", "evidence_passage_id": "u2", "reason": "Payoff"}],
             "value": 3, "reason": "Exchange"}]}
    parsed = schema.model_validate(data).scenes[0]
    assert pauses.convert(parsed, rows).pauses[0].id == "gu1:u2"
    for field, value in [("gap_ref", "g99"), ("evidence_passage_id", "Thought 2.")]:
        invalid = copy.deepcopy(data)
        invalid["scenes"][0]["pauses"][0][field] = value
        with pytest.raises(ValidationError):
            schema.model_validate(invalid)
    assert schema.model_json_schema() == pauses.schema([entry], {"a": rows}).model_json_schema()


def test_no_gaps_means_no_pause_instructions():
    rows = items(1)
    entry = pauses.card(scene("a", "u0", "u0")["beat"], rows)
    schema = pauses.schema([entry], {"a": rows})
    with pytest.raises(ValidationError):
        schema.model_validate({"scenes": [{"id": "a", "spans": [], "value": 1, "reason": "Discard",
            "pauses": [{"gap_ref": "g1", "action": "lead_in", "seconds": 2, "reason": "Unknown"}]}]})


def test_only_bad_pause_is_repaired_and_valid_speech_and_pause_are_locked():
    original = proposal()
    snapshot = original.model_dump()
    evaluator = Repairer({"repairs": [{"slot": 1, "pause": {"gap_ref": "g2", "action": "keep", "evidence_passage_id": "u2", "reason": "Comic timing"}}]})
    value, recovery = pauses.repair(original, items(4), evaluator, "edit-repair-a", [])
    assert original.model_dump() == snapshot
    assert value.spans == original.spans and value.value == original.value and value.reason == original.reason
    assert value.gaps[0].id == original.pauses[0].id and value.gaps[0].seconds == 3
    assert value.gaps[1].evidence == "u2"
    assert recovery == {"repaired_instructions": 1, "fallback_gaps": []}
    assert len(evaluator.calls) == 1
    _, payload, kwargs = evaluator.calls[0]
    assert payload["original_proposal"] == snapshot
    assert [p["slot"] for p in payload["problems"]] == [1]
    assert kwargs["output_retry_delays"] == ()


@pytest.mark.parametrize("kind", ["known", "unknown", "unsafe", "conflict"])
def test_failed_pause_repair_keeps_uncertain_gap_without_rewriting_speech(kind):
    rows = items(4)
    original = proposal()
    if kind == "unknown":
        original.pauses[1].id = "gu1"
    elif kind == "unsafe":
        rows[0]["gap_unsafe"] = True
    elif kind == "conflict":
        original.pauses.append(episode.KeepPause(id="gu0:u1", action="keep", evidence="u1", reason="Different timing"))
    evaluator = Repairer(failure=ModelOutputError("Cannot repair"))
    warnings = []
    value, recovery = pauses.repair(original, rows, evaluator, "edit-repair-a", warnings)
    assert value.spans == original.spans
    assert len(evaluator.calls) == 1 and warnings
    assert "gu1:u2" in recovery["fallback_gaps"]
    retained = episode.compile_scene(value, rows)
    assert any(s["start_us"] <= rows[1]["speech_end_us"] < rows[2]["speech_start_us"] <= s["end_us"] for s in retained)
    for u in rows:
        assert any(s["start_us"] <= u["speech_start_us"] < u["speech_end_us"] <= s["end_us"] for s in retained)
    if kind == "unknown":
        assert len(retained) == 1  # Never guess which pause a malformed reference intended.
    if kind == "known":
        assert value.gaps[0].seconds == 3  # A valid independent lead-in survives.


def test_invalid_speech_is_not_treated_as_pause_only_failure():
    original = proposal(spans=[{"first": "unknown", "last": "u3"}])
    evaluator = Repairer(failure=ModelOutputError("No response"))
    with pytest.raises(ModelAnchorError, match="Invalid range"):
        pauses.repair(original, items(4), evaluator, "edit-repair-a", [])
    assert not evaluator.calls


@pytest.mark.parametrize("failure", [Interrupted("Paused"), ValueError("Service unavailable")])
def test_cancellation_and_service_failures_never_become_pause_fallback(failure):
    with pytest.raises(type(failure), match=str(failure)):
        pauses.repair(proposal(), items(4), Repairer(failure=failure), "edit-repair-a", [])


def test_identical_annotations_are_deduplicated_without_model_calls():
    original = proposal(pauses=[{"id": "gu0:u1", "action": "keep", "evidence": "u1", "reason": "Pause"}]*2)
    evaluator = Repairer()
    value, recovery = pauses.repair(original, items(4), evaluator, "edit-repair-a", [])
    assert len(value.gaps) == 1 and recovery is None and not evaluator.calls


def test_fallback_reason_reaches_editorial_reviewer():
    rows = items(4)
    value, recovery = pauses.repair(proposal(), rows, Repairer(failure=ModelOutputError("Invalid")), "edit-repair-a", [])
    candidate = {"beat": scene("a", "u0", "u3")["beat"], "edit": value.model_dump(), "pause_recovery": recovery}
    cards = review.cards_for([candidate], rows, {"a"}, [])
    assert cards[0]["pause_recovery"]["fallback_gaps"] == ["gu1:u2"]
    assert "Check pause_recovery fallback_gaps explicitly" in review.REVIEW_RULES


def test_ready_batches_continue_while_another_batch_repairs_and_output_stays_ordered():
    third_started, lock = Event(), Lock()
    active = peak = 0
    class Concurrent(Evaluator):
        provider = "codex"
        def call(self, system, prompt, schema, key, validate=None, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                if "-repair-" in key:
                    assert third_started.wait(5), "Later batches were blocked behind an unrelated repair"
                if key == "batch-2":
                    third_started.set()
                value = super().call(system, prompt, schema, key, validate=validate, **kwargs)
                if key == "batch-0":
                    value.scenes[0].spans[0].first = "invented"
                return value
            finally:
                with lock:
                    active -= 1
    rows = items(24)
    beats = [scene(f"b{i}", f"u{i*2}", f"u{i*2+1}")["beat"] for i in range(12)]
    result = episode.edit_scenes(beats, rows, Concurrent(), "batch", "", [])
    assert third_started.is_set() and peak == 2
    assert [s["beat"]["id"] for s in result] == [b["id"] for b in beats]
    assert all(episode.compiled(s, rows) for s in result)
