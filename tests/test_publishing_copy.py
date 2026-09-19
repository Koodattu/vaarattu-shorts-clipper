import json

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import buffer, publishing_copy, publishing_queue
from vaarattu_shorts.web import create_app
from test_buffer import remote as remote
from test_posting_and_cleanup import completed as completed


def sample():
    return publishing_copy.GeneratedCopy(title="Vanha hiiri ei kuole", caption="Uusi tuntee takuuaikansa.")


def test_prompt_uses_only_final_retained_speech_and_guidance():
    body = {"start_us": 10, "end_us": 90,
            "words": [{"id": str(i), "text": text, "start_us": i * 10, "end_us": i * 10 + 5}
                      for i, text in enumerate(["before", "kept", "fluff", "gap", "ending", "after"])],
            "speech_cuts": [{"word_ids": ["2"]}],
            "pacing": {"retained": [{"source_start_us": 10, "source_end_us": 30},
                                     {"source_start_us": 40, "source_end_us": 50}]}}

    class Evaluator:
        verification_budget = 48000
        verification_reasoning = "low"

        def request_size(self, system, prompt, schema):
            return 100

        def call(self, system, prompt, schema, key, **kwargs):
            data = json.loads(prompt)
            assert data["final_speech"] == "kept ending"
            assert data["guidance"] == "Kuiva huumori"
            assert data["previous_copy"]["title"] == "Old title"
            assert schema is publishing_copy.GeneratedCopy
            kwargs["validate"](sample())
            return sample()

    assert publishing_copy.propose(body, {}, Evaluator(), note="Kuiva huumori",
                                  previous={"title": "Old title", "caption": "Old caption"}) == sample()


def test_saved_copy_preserves_ready_stamp_and_expires_on_revision_or_transcript_change(settings, store, completed):
    clip = store.clip("early")
    saved = publishing_copy.save(store, clip, sample().model_dump())
    assert store.clip("early")["review_status"] == "ready_to_post"
    assert store.clip("early")["body"]["title"] == "early"
    assert publishing_copy.get(store, clip) == saved
    changed = {**clip, "revision": 2}
    assert publishing_copy.get(store, changed) is None
    changed = {**clip, "body": {**clip["body"], "words": [{"id": "w", "text": "edited"}]}}
    assert publishing_copy.get(store, changed) is None
    with pytest.raises(ValueError, match="changed elsewhere"):
        publishing_copy.save(store, clip, sample().model_dump())


def test_regeneration_reuses_saved_copy_until_explicitly_requested(settings, store, completed, monkeypatch):
    calls = []

    def propose(settings, store, clip, note, previous):
        calls.append((note, previous))
        return sample()

    monkeypatch.setattr(publishing_copy, "_propose", propose)
    clip = store.clip("early")
    first = publishing_copy.generate(settings, store, clip)
    assert publishing_copy.generate(settings, store, clip) == first
    second = publishing_copy.generate(settings, store, clip, regenerate=True, expected_id=first["id"], note="Dry")
    assert len(calls) == 2 and calls[1] == ("Dry", first)
    assert second["id"] != first["id"] and second["note"] == "Dry"


def test_generation_cannot_save_over_concurrent_edit(settings, store, completed, monkeypatch):
    clip = store.clip("early")

    def propose(*args):
        store.save_clip("early", completed, 2, clip["body"])
        return sample()

    monkeypatch.setattr(publishing_copy, "_propose", propose)
    with pytest.raises(ValueError, match="clip changed"):
        publishing_copy.generate(settings, store, clip)
    assert publishing_copy.get(store, store.clip("early")) is None


def test_copy_api_generates_saves_and_rejects_stale_revision_without_publishing(settings, store, completed, monkeypatch):
    monkeypatch.setattr(publishing_copy, "_propose", lambda *args: sample())
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": client.get("/api/status").json()["token"]}
    url = "/api/publishing/copy/early"
    assert client.post(url + "/generate", json={"revision": 1}).status_code == 403
    response = client.post(url + "/generate", headers=headers, json={"revision": 1, "note": "Dry"})
    assert response.status_code == 200, response.text
    copy = response.json()
    response = client.post(url, headers=headers, json={"revision": 1, "title": "My title", "caption": "My caption.",
                                                      "expected_id": copy["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["generated"] is False
    assert client.post(url + "/generate", headers=headers, json={"revision": 2}).status_code == 400
    assert buffer.publications(store) == {}
    assert store.clip("early")["review_status"] == "ready_to_post"


def test_fill_uses_manual_copy_and_generates_missing_copy_before_sending(settings, store, remote, monkeypatch):
    publishing_copy.save(store, store.clip("early"), {"title": "My title", "caption": "My caption."})
    calls = []

    def propose(settings, store, clip, note, previous):
        assert not remote["mutations"]
        calls.append(clip["id"])
        return sample()

    monkeypatch.setattr(publishing_copy, "_propose", propose)
    assert publishing_queue.fill(settings, store)["scheduled"] == 2
    assert calls == ["late"]
    assert [p["text"] for p in remote["mutations"]] == ["My caption."] * 3 + [sample().caption] * 3
    yt = [p["metadata"]["youtube"]["title"] for p in remote["mutations"] if p["channelId"] == "youtube"]
    assert yt == ["My title", sample().title]
    with pytest.raises(ValueError, match="publishing request"):
        publishing_copy.save(store, store.clip("early"), sample().model_dump())


def test_generation_failure_stops_fill_before_planning_or_upload(settings, store, remote, monkeypatch):
    def propose(settings, store, clip, note, previous):
        if clip["id"] == "late":
            raise ValueError("Model unavailable")
        return sample()

    monkeypatch.setattr(publishing_copy, "_propose", propose)
    with pytest.raises(ValueError, match="Model unavailable"):
        publishing_queue.fill(settings, store)
    assert publishing_copy.get(store, store.clip("early")) is not None
    assert not remote["mutations"] and not remote["objects"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM posting_plan").fetchone()[0] == 0


def test_preview_uses_saved_copy_when_text_omitted(settings, store, remote):
    publishing_copy.save(store, store.clip("early"), sample().model_dump())
    ticket = buffer.preview(settings, store, [{"clip_id": "early", "revision": 1}], "now")
    assert ticket["items"][0]["title"] == sample().title
    assert ticket["items"][0]["caption"] == sample().caption
    assert not remote["mutations"]
