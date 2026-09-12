import json

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import caption_correction as captions
from vaarattu_shorts import pipeline, worker
from vaarattu_shorts.llm import ModelAnchorError
from vaarattu_shorts.storage import atomic_json
from vaarattu_shorts.web import create_app


@pytest.fixture
def caption_clip(settings, store):
    run = store.admit({"video": "aaaaaaaaaaa", "provider": "codex", "budget_usd": 0}, "captions")
    words = [
        {"id": f"w{i}", "text": word, "start_us": i * 1000000, "end_us": i * 1000000 + 800000}
        for i, word in enumerate(["minä", "revin", "mun", "pelihausta", "taas"])
    ]
    folder = settings.ready / "clip" / "1"
    folder.mkdir(parents=True)
    (folder / "short.mp4").write_bytes(b"preview")
    body = dict(
        words=words, start_us=0, end_us=5000000, title="Unrelated title", status="ready", folder=str(folder)
    )
    store.save_clip("clip", run, 1, body)
    store.update(run, state="completed", result={"outcome": "clips_ready"})
    atomic_json(
        settings.work / "runs" / run / "asr" / "transcript.json", {"words": words, "duration_us": 6000000}
    )
    return run, body


def change(**kwargs):
    return {
        "word_id": "w3",
        "original": "pelihausta",
        "replacement": "pelihousut",
        "reason": "Ilmauksen sana sopii ympäröivään puheeseen.",
        **kwargs,
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"word_id": "unknown"},
        {"original": "wrong"},
        {"replacement": "two words"},
        {"replacement": ""},
        {"replacement": "pelihausta"},
        {"replacement": "\nnew\nword"},
        {"replacement": "!"},
        {"replacement": "x" * 60},
    ],
)
def test_invalid_word_changes_never_apply(caption_clip, patch):
    _, body = caption_clip
    with pytest.raises(ValueError):
        captions.apply(body, [change(**patch)])
    assert body["words"][3]["text"] == "pelihausta"


def test_originals_timing_order_and_undo_are_preserved(caption_clip):
    _, body = caption_clip
    revised = captions.apply(body, [change()], automatic=True)
    assert revised["words"][3]["text"] == "pelihousut"
    assert revised["words"][:3] == body["words"][:3]
    assert [(w["id"], w["start_us"], w["end_us"]) for w in revised["words"]] == [
        (w["id"], w["start_us"], w["end_us"]) for w in body["words"]
    ]
    assert captions.undo(revised)["words"] == body["words"]
    revised["words"][3]["text"] = "manual"
    with pytest.raises(ValueError, match="edited after"):
        captions.undo(revised)
    with pytest.raises(ValueError):
        captions.apply({**body, "caption_locked_word_ids": ["w3"]}, [change()])
    with pytest.raises(ValueError):
        captions.apply(body, [change(), change()])


def test_prompt_contains_speech_only_and_empty_changes_are_valid(caption_clip):
    _, body = caption_clip

    class Evaluator:
        verification_budget = 48000
        verification_reasoning = "low"

        def request_size(self, *_):
            return 100

        def call(self, system, prompt, schema, key, **kwargs):
            assert "Unrelated title" not in prompt
            assert "pelihausta" not in system and "pelihousut" not in system
            assert "start_us" not in prompt
            assert json.loads(prompt)["change_limit"] == 1
            result = schema(changes=[])
            kwargs["validate"](result)
            return result

    proposal = captions.propose(body, {"words": body["words"]}, Evaluator())
    assert captions.apply(body, proposal.changes)["words"] == body["words"]


def test_manual_check_worker_apply_and_undo(settings, store, caption_clip, monkeypatch):
    run, original = caption_clip
    monkeypatch.setattr(
        pipeline.Pipeline, "caption_proposal", lambda *_: captions.Corrections(changes=[change()])
    )
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": client.get("/api/status").json()["token"]}
    response = client.post("/api/clips/clip/caption-check", headers=headers, json={"expected_revision": 1})
    assert response.status_code == 202
    assert (
        client.post(
            "/api/clips/clip/caption-check", headers=headers, json={"expected_revision": 1}
        ).status_code
        == 400
    )
    assert store.claim() == run
    worker.run_job(settings, store, run, lambda: False)
    checked = store.clip("clip")
    assert checked["revision"] == 1 and checked["body"]["words"] == original["words"]
    assert store.get(run)["state"] == "completed"
    check_id = checked["body"]["caption_check"]["id"]
    for data in [
        dict(expected_revision=2, check_id=check_id, word_ids=["w3"]),
        dict(expected_revision=1, check_id="stale", word_ids=["w3"]),
        dict(expected_revision=1, check_id=check_id, word_ids=["unknown"]),
    ]:
        assert (
            client.post("/api/clips/clip/caption-corrections", headers=headers, json=data).status_code == 400
        )
    response = client.post(
        "/api/clips/clip/caption-corrections",
        headers=headers,
        json={"expected_revision": 1, "check_id": check_id, "word_ids": ["w3"]},
    )
    assert response.status_code == 202
    revised = store.clip("clip")
    assert revised["revision"] == 2 and revised["body"]["words"][3]["text"] == "pelihousut"
    assert revised["body"]["previous_revision"] == 1
    assert store.get(run)["result"]["rerender_requested"]
    store.update(run, state="completed")
    response = client.post("/api/clips/clip/caption-undo", headers=headers, json={"expected_revision": 2})
    assert response.status_code == 202 and store.clip("clip")["body"]["words"] == original["words"]


def test_automatic_pass_is_opt_in_resumable_and_fails_closed(settings, store, caption_clip, monkeypatch):
    run, body = caption_clip
    store.save_clip("clip", run, 1, {**body, "status": "pending"})
    calls = []

    def propose(*_):
        calls.append(1)
        return captions.Corrections(changes=[change()])

    monkeypatch.setattr(pipeline.Pipeline, "caption_proposal", propose)
    process = pipeline.Pipeline(settings, store, run)
    process.correct_captions({"words": body["words"]})
    assert not calls
    process.config["correct_captions"] = True
    process.correct_captions({"words": body["words"]})
    process.correct_captions({"words": body["words"]})
    assert len(calls) == 1 and store.clip("clip")["body"]["words"][3]["text"] == "pelihousut"
    process.config["final_transcription"] = True
    process.refine_captions(None, 6000000)  # Applied corrections must bypass subsequent ASR.
    store.save_clip("clip", run, 1, {**body, "status": "pending"})

    def invalid(*_):
        raise ModelAnchorError("invalid edit")

    monkeypatch.setattr(pipeline.Pipeline, "caption_proposal", invalid)
    process.correct_captions({"words": body["words"]})
    assert store.clip("clip")["body"]["words"] == body["words"]
    assert store.clip("clip")["body"]["caption_correction_warning"]
