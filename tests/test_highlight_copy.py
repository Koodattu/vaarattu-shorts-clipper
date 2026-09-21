import json

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import highlight_copy as copy, highlights
from vaarattu_shorts.storage import atomic_json, digest
from vaarattu_shorts.web import create_app


@pytest.fixture
def finished(settings):
    store = highlights.store_for(settings)
    run_id = store.admit({"provider": "codex", "budget_usd": 0, "context_size": 32768,
                          "manifest": {"title": "Recording", "sources": []}}, "copy-test")
    folder = highlights.revision_folder(settings, run_id, 1)
    plan = {"retained": [{"asset": "s0", "start_us": 10000000, "end_us": 30000000, "sequence": "b1"}],
            "rankings": [{"id": "b1", "score": 90}]}
    atomic_json(folder / "plan.json", plan)
    (folder / "draft.mp4").write_bytes(b"rendered")
    transcript = {"words": [{"start_us": 0, "end_us": 5000000, "text": "EXCLUDED"},
                            {"start_us": 11000000, "end_us": 20000000, "text": "Retained speech"},
                            {"start_us": 29000000, "end_us": 40000000, "text": "OUTSIDE"}]}
    atomic_json(highlights.run_folder(settings, run_id) / "s0" / "asr" / "transcript.json", transcript)
    store.update(run_id, state="completed", result={"revision": 1, "has_draft": True,
                 "plan_sha256": digest(folder / "plan.json")})
    return store, run_id, folder, plan, {"s0": transcript}


class Evaluator:
    verification_budget = 48000

    def request_size(self, system, prompt, schema):
        return len(prompt)

    def call(self, system, prompt, schema, key, **kw):
        self.payload = json.loads(prompt)
        self.options = kw
        value = schema(title="A grounded title", caption="One short description.")
        kw["validate"](value)
        return value


def test_copy_sees_only_retained_speech_and_low_reasoning(settings, finished):
    _, _, _, plan, transcripts = finished
    evaluator = Evaluator()
    value = copy.propose(plan, transcripts, evaluator, "Keep it understated")
    assert value.title
    assert evaluator.payload["scenes_in_video_order"] == [{"output_start_seconds": 0, "output_end_seconds": 20, "edited_seconds": 20,
                                                            "quality_score": 90, "speech": "Retained speech", "excerpted": False}]
    assert evaluator.payload["guidance"] == "Keep it understated"
    assert evaluator.options["reasoning_effort"] == "low"
    with pytest.raises(ValueError):
        evaluator.options["validate"](copy.VideoCopy(title="#spam", caption="Text"))


def test_long_video_uses_one_bounded_request_with_all_scenes_represented(finished):
    _, _, _, plan, transcripts = finished
    transcripts["s0"]["words"] = [{"start_us": 11000000, "end_us": 20000000, "text": f"word{i}"} for i in range(2000)]
    plan["retained"].append({**plan["retained"][0], "sequence": "b2"})
    evaluator = Evaluator()
    evaluator.verification_budget = 1000
    copy.propose(plan, transcripts, evaluator)
    assert len(json.dumps(evaluator.payload)) <= 1000
    assert len(evaluator.payload["scenes_in_video_order"]) == 2
    assert all(s["excerpted"] for s in evaluator.payload["scenes_in_video_order"])
    assert all("word0" in s["speech"] and "word1999" in s["speech"] for s in evaluator.payload["scenes_in_video_order"])


def test_save_protects_human_text_and_revision_identity(settings, finished):
    store, run_id, folder, _, _ = finished
    saved = copy.save(settings, store, run_id, 1, {"title": "Human title", "caption": "Human description"})
    assert copy.get(settings, store.get(run_id)) == saved
    with pytest.raises(ValueError, match="changed"):
        copy.save(settings, store, run_id, 1, {"title": "Overwrite", "caption": "Bad"})
    with pytest.raises(ValueError, match="changed"):
        copy.save(settings, store, run_id, 2, {"title": "Wrong revision", "caption": "Bad"})
    atomic_json(folder / "plan.json", {"retained": []})
    with pytest.raises(ValueError, match="changed"):
        copy.save(settings, store, run_id, 1, {"title": "Wrong edit", "caption": "Bad"},
                  expected_id=saved["id"], plan_hash=saved["identity"][1])


def test_generation_does_not_render_or_change_approval(settings, finished, monkeypatch):
    store, run_id, folder, _, _ = finished
    run = store.get(run_id)
    store.update(run_id, result={**run["result"], "review": "approved", "has_final": True})
    evaluator = Evaluator()
    monkeypatch.setattr(highlights, "Evaluator", lambda *args, **kw: evaluator)
    before = store.get(run_id)["result"]
    saved = copy.generate(settings, store, run_id, 1)
    assert saved["generated"] and saved["identity"] == [1, digest(folder / "plan.json")]
    assert store.get(run_id)["result"] == before
    assert (folder / "draft.mp4").read_bytes() == b"rendered"
    store.update(run_id, state="running")
    monkeypatch.setattr(copy, "propose", lambda *args: pytest.fail("Final rendering must reuse existing text"))
    assert copy.automatic(settings, store, run_id, 1) == saved


def test_changed_revision_during_inference_discards_output(settings, finished, monkeypatch):
    store, run_id, folder, _, _ = finished
    monkeypatch.setattr(highlights, "Evaluator", lambda *args, **kw: Evaluator())
    def change(*args):
        store.update(run_id, result={"revision": 2})
        return copy.VideoCopy(title="Old", caption="Old")
    monkeypatch.setattr(copy, "propose", change)
    with pytest.raises(ValueError, match="changed"):
        copy.generate(settings, store, run_id, 1)
    assert not (folder / "publishing-copy.json").exists()


def test_manual_and_generated_text_api_require_local_token(settings, finished, monkeypatch):
    store, run_id, _, _, _ = finished
    monkeypatch.setattr(highlights, "Evaluator", lambda *args, **kw: Evaluator())
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        url = f"/api/highlights/{run_id}/copy"
        assert client.post(url+"/generate", json={"revision": 1}).status_code == 403
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        response = client.post(url+"/generate", headers=headers, json={"revision": 1})
        assert response.status_code == 200, response.text
        saved = response.json()
        assert client.get("/api/highlights").json()[0]["publishing_copy"] == saved
        response = client.post(url, headers=headers, json={"revision": 1, "title": "My title", "caption": "My description", "expected_id": saved["id"]})
        assert response.status_code == 200, response.text
        assert response.json()["generated"] is False
        assert client.post(url, headers=headers, json={"revision": 1, "title": "Stale", "caption": "Stale", "expected_id": saved["id"]}).status_code == 400


def test_manual_save_during_generation_is_not_overwritten(settings, finished, monkeypatch):
    store, run_id, _, _, _ = finished
    monkeypatch.setattr(highlights, "Evaluator", lambda *args, **kw: Evaluator())
    def manual_edit(*args):
        copy.save(settings, store, run_id, 1, {"title": "Human edit", "caption": "Keep this"})
        return copy.VideoCopy(title="Generated", caption="Late response")
    monkeypatch.setattr(copy, "propose", manual_edit)
    with pytest.raises(ValueError, match="changed"):
        copy.generate(settings, store, run_id, 1)
    assert copy.get(settings, store.get(run_id))["title"] == "Human edit"


def test_no_retained_speech_does_not_request_invented_copy(finished):
    _, _, _, plan, transcripts = finished
    transcripts["s0"]["words"] = []
    evaluator = Evaluator()
    with pytest.raises(ValueError, match="No retained speech"):
        copy.propose(plan, transcripts, evaluator)
    assert not hasattr(evaluator, "payload")


def test_scene_coverage_uses_edited_duration_not_source_gaps(finished):
    _, _, _, plan, transcripts = finished
    plan["retained"] = [
        {"asset": "s0", "start_us": 10000000, "end_us": 30000000, "sequence": "b1"},
        {"asset": "s0", "start_us": 100000000, "end_us": 110000000, "sequence": "b1"},
        {"asset": "s1", "start_us": 400000000, "end_us": 520000000, "sequence": "b2"},
    ]
    transcripts["s0"]["words"].append({"start_us": 101000000, "end_us": 109000000, "text": "Payoff"})
    transcripts["s1"] = {"words": [{"start_us": 401000000, "end_us": 519000000, "text": "Long discussion"}]}
    evaluator = Evaluator()
    copy.propose(plan, transcripts, evaluator)
    cards = evaluator.payload["scenes_in_video_order"]
    assert evaluator.payload["video_duration_seconds"] == 150
    assert [(c["output_start_seconds"], c["output_end_seconds"], c["edited_seconds"]) for c in cards] == [(0, 30, 30), (30, 150, 120)]
    assert "EXCLUDED" not in json.dumps(cards)


def test_long_description_roundtrips_without_expanding_short_clip_schema(settings, finished):
    store, run_id, _, _, _ = finished
    description = "First paragraph about the whole edited recording. " * 8 + "\n\n" + "More context from later conversations. " * 8
    assert len(description) > 280
    value = copy.VideoCopy(title="Whole video title", caption=description)
    copy.save(settings, store, run_id, 1, value.model_dump())
    assert copy.get(settings, store.get(run_id))["caption"] == description.strip()
    with pytest.raises(ValueError):
        copy.publishing_copy.GeneratedCopy(title=value.title, caption=description)


def test_regeneration_does_not_anchor_on_old_copy_or_old_guidance(settings, finished, monkeypatch):
    store, run_id, _, _, _ = finished
    previous = copy.save(settings, store, run_id, 1, {"title": "Only the opening joke", "caption": "Old framing"}, note="Focus only on the first scene")
    evaluator = Evaluator()
    monkeypatch.setattr(highlights, "Evaluator", lambda *args, **kw: evaluator)
    copy.generate(settings, store, run_id, 1, expected_id=previous["id"])
    assert evaluator.payload["guidance"] == ""
    assert "previous_copy" not in evaluator.payload
    assert "Old framing" not in json.dumps(evaluator.payload)
    assert "Focus only" not in json.dumps(evaluator.payload)
