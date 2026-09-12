import hashlib
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from vaarattu_shorts import discover
from vaarattu_shorts.contracts import Candidate, Proposals, Word
from vaarattu_shorts.llm import Evaluator, ModelAnchorError
from vaarattu_shorts.pipeline import Pipeline
from vaarattu_shorts.storage import atomic_json, digest
from vaarattu_shorts.web import create_app


def speech(count=100):
    return {
        "duration_us": count * 1000000,
        "words": [
            Word(id=f"w{i}", start_us=i * 1000000, end_us=(i + 1) * 1000000, text="ajatus.").model_dump()
            for i in range(count)
        ],
    }


def candidate(start, end, **changes):
    return Candidate.model_validate(
        {
            "outcome": "accept",
            "start_word_id": f"w{start}",
            "end_word_id": f"w{end}",
            "idea_word_id": f"w{start}",
            "category": "observation",
            "summary_fi": "Havainto",
            "title_fi": "Ajatus",
            "reason": "Konkreettinen havainto.",
            "scores": dict(standalone=3, substance=3, fidelity=3, opening=2, payoff=2),
            "flags": [],
            **changes,
        }
    )


def verified(c, eligible=True):
    return {
        "candidate": c.model_dump(),
        "start_us": int(c.start_word_id[1:]) * 1000000,
        "end_us": (int(c.end_word_id[1:]) + 1) * 1000000,
        "eligible": eligible,
    }


def test_real_interior_anchors_expand_but_unknown_ids_never_do():
    words = [Word.model_validate(w) for w in speech(20)["words"]]
    for i, word in enumerate(words):
        word.text = "ajatus." if i % 5 == 4 else "niin"
    c = candidate(1, 8, idea_word_id="w6")
    fixed = discover.normalize_proposal(c, words)
    assert (fixed.start_word_id, fixed.end_word_id, fixed.idea_word_id) == ("w0", "w9", "w5")
    assert c.start_word_id == "w1"
    with pytest.raises(ModelAnchorError):
        discover.normalize_proposal(candidate(1, 8, idea_word_id="invented"), words)
    with pytest.raises(ModelAnchorError):
        discover.normalize_proposal(candidate(8, 1), words)


def test_global_shortlist_caps_context_work_and_retains_every_exclusion(settings, store):
    run = store.admit({}, "shortlist")
    proposed = [candidate(i * 5, i * 5 + 3) for i in range(60)]
    for c in proposed[40:]:
        c.scores.substance = 4
    proposed[-1].outcome = "needs_context"
    fuller = proposed[-1].model_copy(update={"end_word_id": "w299"})
    proposed += [fuller, candidate(310, 313, outcome="reject"), candidate(320, 323)]
    proposed[-1].scores.substance = 2
    calls = []

    class FixtureEvaluator(Evaluator):
        def call(self, system, prompt, schema, key, **kwargs):
            calls.append(key)
            if schema is Proposals:
                return Proposals(candidates=proposed, feedback="Fixture candidates.")
            a, b = re.search(r"Proposed start=w(\d+), end=w(\d+)", prompt).groups()
            result = candidate(int(a), int(b))
            kwargs["validate"](result)
            return result

    result = discover.discover(speech(360), FixtureEvaluator(
        "openai", store, run, settings.work, 1, lambda: None), lambda _: None)
    assert len(calls) == 21  # One discovery plus twenty candidates, including the promising context gap.
    assert len(result["proposals"]) == 63
    assert len(result["verified"]) == 20
    assert {v["proposal"]["start_word_id"] for v in result["verified"]} == {
        f"w{i * 5}" for i in range(40, 60)}
    assert result["verified"][-1]["proposal"]["end_word_id"] == "w299"
    reasons = [i["reason"] for i in result["issues"]]
    assert reasons.count("duplicate_moment") == 1
    assert reasons.count("insufficient_editorial_value") == 2
    assert reasons.count("outside_verification_budget") == 40
    assert result["verification_shortlist"]["selected"] == 20
    assert all(i["candidate"] and i["interval"] for i in result["issues"])


def test_weak_discovery_never_triggers_context_work(settings, store):
    run = store.admit({"video": "abc_def-ghI"}, "weak")
    calls, progress = [], []

    class FixtureEvaluator(Evaluator):
        def call(self, system, prompt, schema, key, **kwargs):
            calls.append(key)
            assert schema is Proposals
            c = candidate(0, 5)
            c.scores.substance = 2
            return Proposals(candidates=[c], feedback="Passing remark.")

    result = discover.discover(speech(30), FixtureEvaluator(
        "openai", store, run, settings.work, 1, lambda: None), progress.append)
    assert len(calls) == 1 and result["verified"] == []
    assert result["verification_shortlist"]["selected"] == 0
    assert progress[-1] == 1
    finished = Pipeline(settings, store, run).finish({"status": "unavailable"}, result)
    assert finished["outcome"] == "no_candidates"
    assert finished["verification_shortlist"]["selected"] == 0


def test_missing_empty_section_feedback_gets_bounded_repair_and_is_saved(settings, store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    run = store.admit({}, "feedback")
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        result = {"candidates": []}
        if len(calls) == 2:
            result["feedback"] = "Puhe käsitteli pelin varusteita ilman itsenäistä havaintoa."
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": json.dumps(result)}]}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("openai", store, run, settings.work, 1, lambda: None, client)
        result = discover.discover(speech(10), evaluator, lambda _: None)
        assert discover.discover(speech(10), evaluator, lambda _: None) == result
    assert len(calls) == 2
    assert result["section_feedback"][0]["candidates"] == 0
    assert result["section_feedback"][0]["feedback"].startswith("Puhe käsitteli")
    assert (
        json.loads((settings.work / "section-feedback.json").read_text("utf-8")) == result["section_feedback"]
    )
    for feedback in ["", "   ", "x" * 241]:
        with pytest.raises(ValidationError):
            Proposals(candidates=[], feedback=feedback)


def test_recovery_rechecks_exclusions_without_discovery_and_keeps_winners(settings, store):
    run = store.admit({}, "recover")
    winner = verified(candidate(0, 9))
    seed = {
        "proposals": [
            candidate(0, 9).model_dump(),
            candidate(20, 29, flags=["Game terms"]).model_dump(),
            candidate(40, 43).model_dump(),
        ],
        "verified": [winner],
        "coverage": [[0, 100000000]],
        "issues": [{"reason": "invalid_anchors", "candidate": candidate(60, 69).model_dump()}],
    }
    calls = []

    class FixtureEvaluator(Evaluator):
        def call(self, system, prompt, schema, key, **kwargs):
            assert schema is Candidate
            calls.append(key)
            a, b = re.search(r"Proposed start=w(\d+), end=w(\d+)", prompt).groups()
            result = candidate(int(a), int(b), flags=["Advisory note"])
            kwargs["validate"](result)
            return result

    result = discover.discover(
        speech(),
        FixtureEvaluator("openai", store, run, settings.work, 1, lambda: None),
        lambda _: None,
        seed=seed,
    )
    assert len(calls) == 3
    assert result["verified"][0] == winner
    assert len(result["verified"]) == 4 and all(v["eligible"] for v in result["verified"])
    assert any(v["end_us"] - v["start_us"] == 4000000 for v in result["verified"])
    assert result["section_feedback"] == []  # Never invent feedback for an older scan.
    assert result["coverage"] == seed["coverage"]


@pytest.mark.parametrize("earlier_recovery", [False, True])
def test_recovery_pipeline_preserves_exports_and_original_checkpoints(
    settings, store, monkeypatch, earlier_recovery
):
    run = store.admit(
        {"video": "abc_def-ghI", "provider": "openai", "budget_usd": 1, "layout": {}}, "pipeline"
    )
    pipeline = Pipeline(settings, store, run)
    audio = pipeline.folder / "source.opus"
    audio.write_bytes(b"fixture")
    winner = verified(candidate(0, 9))
    excluded = verified(candidate(20, 29), False)
    original = {
        "version": "conversation-v5",
        "verified": [winner, excluded],
        "proposals": [winner["candidate"], excluded["candidate"]],
        "coverage": [[0, 100000000]],
        "issues": [],
    }
    values = {
        "metadata": {"id": "abc_def-ghI", "title": "VOD", "upload_date": "20260708"},
        "audio": {"path": str(audio), "duration": 100},
        "transcript": speech(),
        "selection": original,
    }
    for name, value in values.items():
        atomic_json(pipeline.folder / f"{name}.checkpoint.json", {"result": value, "artifacts": []})
    before = {p: digest(p) for p in pipeline.folder.glob("*.checkpoint.json")}
    old_clip = {"status": "ready", "folder": "unchanged", "start_us": 0, "end_us": 10000000}
    store.save_clip("existing", run, 3, old_clip)
    recovered_clip = {
        "status": "ready",
        "folder": "earlier-recovery",
        "start_us": 19000000,
        "end_us": 31000000,
        "selection": excluded["candidate"],
    }
    if earlier_recovery:
        store.save_clip("earlier", run, 4, recovered_clip)
    store.update(run, state="completed")
    store.control(run, "recheck")
    calls = []

    def recover(transcript, evaluator, progress, chat, seed):
        calls.append(True)
        assert seed == {
            **original,
            "verified": original["verified"] + ([{**excluded, "eligible": True}] if earlier_recovery else []),
        }
        return {**seed, "verified": [winner, {**excluded, "eligible": True}]}

    def deliver(self, clip, source, duration):
        assert source == audio
        self.store.save_clip(
            clip["id"], run, clip["revision"], {**clip["body"], "status": "ready", "folder": "fixture"}
        )

    monkeypatch.setattr(discover, "discover", recover)
    monkeypatch.setattr(Pipeline, "deliver", deliver)
    result = pipeline.recheck_selection()
    assert len(store.clips(run)) == 2 and len(calls) == 1
    assert store.clip("existing")["body"] == old_clip and store.clip("existing")["revision"] == 3
    if earlier_recovery:
        assert store.clip("earlier")["body"] == recovered_clip
        assert store.clip("earlier")["revision"] == 4
    assert result["recovery_version"] == discover.VERSION
    assert not store.get(run)["result"].get("selection_recheck_requested")
    assert all(digest(p) == expected for p, expected in before.items())
    Pipeline(settings, store, run).recheck_selection()
    assert len(store.clips(run)) == 2 and len(calls) == 1


def test_recheck_action_is_guarded_and_survives_pause_resume(settings):
    app = create_app(settings)
    store = app.state.store
    run = store.admit({}, "recheck")
    store.update(run, state="completed")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        url = f"/api/runs/{run}/recheck"
        assert client.post(url, headers=headers).status_code == 400
        atomic_json(settings.work / "runs" / run / "selection.checkpoint.json", {})
        assert client.get(f"/api/runs/{run}").json()["can_recheck"]
        assert client.post(url).status_code == 403
        assert client.post(url, headers=headers).status_code == 200
        assert client.post(url, headers=headers).status_code == 400
        store.control(run, "pause")
        store.control(run, "resume")
        assert store.get(run)["result"]["selection_recheck_requested"]
        store.update(run, state="completed", result={"recovery_version": discover.VERSION})
        assert not client.get(f"/api/runs/{run}").json()["can_recheck"]
        assert client.post(url, headers=headers).status_code == 400


def test_legacy_partial_delivery_keeps_ranked_clip_ids(settings, store, monkeypatch):
    run = store.admit({"video": "abc_def-ghI", "layout": {}, "max_clips": 2}, "legacy")
    ids = [hashlib.sha256(f"{run}:{i}".encode()).hexdigest()[:32] for i in range(2)]
    store.save_clip(ids[0], run, 1, {"status": "ready", "start_us": 0, "end_us": 10000000})

    def deliver(self, clip, *_):
        self.store.save_clip(clip["id"], run, 1, {**clip["body"], "status": "ready"})

    monkeypatch.setattr(Pipeline, "deliver", deliver)
    Pipeline(settings, store, run).export_selection(
        {
            "version": "passages-v2",
            "chat": {"status": "unavailable"},
            "verified": [verified(candidate(0, 9)), verified(candidate(20, 29))],
        },
        speech(),
        {"id": "abc_def-ghI", "title": "VOD", "upload_date": "20260708"},
        settings.work / "fixture",
    )
    assert {c["id"] for c in store.clips(run)} == set(ids)
