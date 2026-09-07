import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts.contracts import Proposals
from vaarattu_shorts.llm import Evaluator, full_offload, rates
from vaarattu_shorts.models import CATALOG
from vaarattu_shorts.web import create_app


def test_gpu_offload_is_a_hard_gate():
    assert full_offload("offloaded 63/63 layers to GPU")
    assert not full_offload("offloaded 40/63 layers to GPU")
    assert not full_offload("model loaded")


def test_rate_expiry_and_reasoning_settings(settings, store, monkeypatch):
    assert rates("zai", datetime(2026, 9, 9, 15, 59, tzinfo=timezone.utc)) == (0.075, 0.25)
    assert rates("zai", datetime(2026, 9, 9, 16, tzinfo=timezone.utc)) == (0.15, 0.5)
    for provider, key in (
        ("gemini", "GEMINI_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("zai", "ZAI_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
    ):
        monkeypatch.setenv(key, "test-secret")
        evaluator = Evaluator(provider, store, "fake", settings.work, 1, lambda: None)
        url, headers, body = evaluator._request("system", "prompt", {"type": "object"})
        assert "test-secret" not in url
        if provider == "gemini":
            assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "low"
        if provider == "zai":
            assert body["thinking"]["type"] == "enabled"
        if provider == "deepseek":
            assert body["thinking"]["type"] == "disabled"
        if provider == "openai":
            assert body["store"] is False and body["reasoning"]["effort"] == "none"


def test_api_parse_repair_and_budget_are_real_orchestration(settings, store, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    run = store.admit({}, "api")
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "bad json"
                            if len(calls) == 1
                            else '{"candidates":[],"feedback":"Game mechanics without a standalone point."}'
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("deepseek", store, run, settings.work, 1, lambda: None, client)
        assert evaluator.call("system", "prompt", Proposals, "one").candidates == []
        assert evaluator.call("system", "prompt", Proposals, "one").candidates == []
    assert len(calls) == 2
    assert store.get(run)["spent"] > 0
    assert store.get(run)["reserved"] == pytest.approx(0)


def test_api_timeout_keeps_reservation(settings, store, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    run = store.admit({}, "timeout")

    def handle(request):
        raise httpx.ReadTimeout("test")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("deepseek", store, run, settings.work, 1, lambda: None, client)
        with pytest.raises(ValueError, match="could not complete"):
            evaluator.call("system", "prompt", Proposals, "one")
    assert store.get(run)["reserved"] > 0


def test_project_cache_overrides_global_environment(settings, monkeypatch):
    monkeypatch.setenv("HF_HOME", "C:/global-cache")
    env = settings.environment()
    assert env["HF_HOME"].startswith(str(settings.cache))
    assert env["TORCH_HOME"].startswith(str(settings.cache))
    assert env["HF_HUB_OFFLINE"] == "1"
    assert set(CATALOG) == {"turbo", "gemma4-31b", "gemma4-26b-a4b"}


def test_local_api_origin_token_and_no_model_download(settings, monkeypatch):
    def forbidden(*_, **__):
        raise AssertionError("Models must not be prepared during app creation")

    monkeypatch.setattr("vaarattu_shorts.models.prepare", forbidden)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        state = client.get("/api/status").json()
        assert set(state["models"].values()) == {False}
        assert not state["providers"]["meta"]["available"]
        assert client.get("/api/status", headers={"Host": "evil.test"}).status_code == 403
        assert client.post("/api/layouts", json={}).status_code == 403
        assert client.get("/api/status", headers={"Origin": "https://evil.test"}).status_code == 403
        token = {"X-Local-Token": state["token"]}
        body = {
            "name": "test",
            "camera": {"x": 0, "y": 0, "width": 0.25, "height": 0.25},
            "gameplay": {"x": 0.3, "y": 0.3, "width": 0.7, "height": 0.7},
            "calibrated": True,
            "solo_host": True,
        }
        response = client.post("/api/layouts", json=body, headers=token)
        assert response.status_code == 201
        assert client.get("/").status_code == 200
        assert client.get("/api/artifacts/missing/video").status_code == 404
