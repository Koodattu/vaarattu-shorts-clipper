import json

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import caption_correction, context_repair, pipeline, tighten
from vaarattu_shorts.web import create_app


@pytest.fixture
def analysis_clip(settings, store):
    run = store.admit({"provider": "codex", "budget_usd": 7, "local_model": "gemma4-31b"}, "analysis")
    folder = settings.ready / "clip" / "1"
    folder.mkdir(parents=True)
    (folder / "short.mp4").write_bytes(b"preview")
    words = [{"id": "w1", "text": "Moi", "start_us": 0, "end_us": 4000000}]
    store.save_clip("clip", run, 1, {"status": "ready", "folder": str(folder), "title": "Clip",
                                   "start_us": 0, "end_us": 4000000, "words": words})
    store.update(run, state="completed", result={})
    transcript = settings.work / "runs" / run / "asr" / "transcript.json"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"words": words}), encoding="utf-8")
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": client.get("/api/status").json()["token"]}
    return run, client, headers


@pytest.mark.parametrize("action,body_key,method", [
    ("context", "context_request", "repair_context"),
    ("tighten", "tighten_check", "suggest_tighter_edit"),
    ("instructed-edit", "tighten_check", "suggest_tighter_edit"),
    ("caption-check", "caption_check", "check_captions"),
])
@pytest.mark.parametrize("provider", ["openai", "codex", None])
def test_action_provider_is_durable_and_used_by_worker(settings, store, analysis_clip, monkeypatch,
                                                     action, body_key, method, provider):
    run, client, headers = analysis_clip
    original = store.get(run)["config"]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    data = {"expected_revision": 1, "note": "Keep the point"}
    if provider:
        data["analysis"] = {"provider": provider, "budget_usd": 2}
    response = client.post(f"/api/clips/clip/{action}", headers=headers, json=data)
    assert response.status_code == 202, response.text
    saved = store.clip("clip")["body"][body_key]
    assert (saved.get("analysis") or {}).get("provider") == provider
    seen = []

    def evaluator(*args, **kwargs):
        seen.append((args[0], args[4], kwargs))
        return object()

    monkeypatch.setattr(pipeline, "Evaluator", evaluator)
    monkeypatch.setattr(caption_correction, "propose", lambda *_: caption_correction.Corrections(changes=[]))
    monkeypatch.setattr(tighten, "propose", lambda *_: {"cuts": [], "summary": "Keep it", "fingerprint": "test"})
    # clip_proposal's default argument is bound at import time; intercept its actual proposal call.
    if action == "caption-check":
        original_proposal = pipeline.Pipeline.clip_proposal

        def caption_proposal(self, clip, transcript, folder, **kwargs):
            return original_proposal(self, clip, transcript, folder,
                                     propose=caption_correction.propose, **kwargs)
        monkeypatch.setattr(pipeline.Pipeline, "clip_proposal", caption_proposal)
    monkeypatch.setattr(context_repair, "propose", lambda *_: (
        context_repair.ContextProposal(outcome="unchanged", start_word_id="", end_word_id="", reason="Already complete"), None))
    monkeypatch.setattr(pipeline.Pipeline, "finish", lambda *_: None)
    instance = pipeline.Pipeline(settings, store, run)
    getattr(instance, method)()
    assert len(seen) == 1
    assert seen[0][0] == (provider or "codex")
    if provider == "openai":
        assert seen[0][1] == 2
    if provider == "codex":
        assert seen[0][2]["codex_config"]["model"] == "gpt-6-luna"
    assert instance.config == original
    assert store.get(run)["config"] == original


@pytest.mark.parametrize("analysis,status", [
    ({"provider": "unknown"}, 422),
    ({"provider": "openai", "budget_usd": -1}, 422),
    ({"provider": "openai", "budget_usd": 0}, 400),
    ({"provider": "meta", "budget_usd": 2}, 400),
    ({"provider": "local", "local_model": "missing"}, 422),
])
def test_invalid_model_choices_do_not_queue_jobs(analysis_clip, store, monkeypatch, analysis, status):
    run, client, headers = analysis_clip
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    response = client.post("/api/clips/clip/tighten", headers=headers,
                           json={"expected_revision": 1, "analysis": analysis})
    assert response.status_code == status, response.text
    assert store.get(run)["state"] == "completed"
    assert "tighten_check" not in store.clip("clip")["body"]


def test_unconfigured_provider_is_rejected_and_defaults_are_safe(analysis_clip, store, monkeypatch):
    run, client, headers = analysis_clip
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.post("/api/clips/clip/tighten", headers=headers,
                           json={"expected_revision": 1, "analysis": {"provider": "openai", "budget_usd": 2}})
    assert response.status_code == 400
    assert store.get(run)["state"] == "completed"
    assert client.get("/api/clips/clip").json()["analysis_defaults"] == {
        "provider": "codex", "budget_usd": 7, "local_model": "gemma4-31b"}


def test_local_override_uses_selected_model_without_mutating_run(settings, store, analysis_clip, monkeypatch):
    from contextlib import contextmanager
    from vaarattu_shorts import web

    run, client, headers = analysis_clip
    original = store.get(run)["config"]
    monkeypatch.setattr(web, "model_path", lambda _, name: settings.models / name)
    response = client.post("/api/clips/clip/tighten", headers=headers, json={
        "expected_revision": 1, "analysis": {"provider": "local", "local_model": "gemma4-26b-a4b"}})
    assert response.status_code == 202, response.text
    seen = []

    @contextmanager
    def server(settings, config, folder, check):
        seen.append(config)
        yield "local-client"

    def evaluator(*args, **kwargs):
        assert args[0] == "local" and args[6] == "local-client"
        return object()

    monkeypatch.setattr(pipeline, "local_server", server)
    monkeypatch.setattr(pipeline, "Evaluator", evaluator)
    monkeypatch.setattr(tighten, "propose", lambda *_: {"cuts": [], "summary": "Keep it", "fingerprint": "test"})
    pipeline.Pipeline(settings, store, run).suggest_tighter_edit()
    assert seen[0]["local_model"] == "gemma4-26b-a4b"
    assert seen[0]["context_size"] == 16384
    assert store.get(run)["config"] == original
