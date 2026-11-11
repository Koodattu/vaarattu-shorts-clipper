import json

import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts.contracts import Proposals
from vaarattu_shorts.model_choices import save_preference
from vaarattu_shorts.pipeline import Pipeline
from vaarattu_shorts.web import create_app


def test_overriding_local_run_keeps_transcription_pins_and_drops_unused_gguf(settings, store):
    config = {
        "provider": "local",
        "local_model": "gemma4-31b",
        "model_manifests": {"turbo": "asr-pin", "gemma4-31b": "gguf-pin"},
    }
    run = store.admit(config, "local-run")
    save_preference(settings, "codex:gpt-6.1-terra", None)
    assert Pipeline(settings, store, run).config["model_manifests"] == {"turbo": "asr-pin"}
    save_preference(settings, "default", None)
    assert Pipeline(settings, store, run).config == config


@pytest.mark.parametrize("provider", ["openai", "codex"])
def test_workspace_model_reaches_requests_and_survives_restart(settings, store, monkeypatch, provider):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    monkeypatch.setenv("CODEX_BASE_URL", "http://127.0.0.1:18080/v1")
    monkeypatch.setattr("vaarattu_shorts.web.preflight", lambda *_: {})
    terra = "gpt-6.1-terra"
    choice = f"{provider}:{terra}"
    paid = provider == "openai"
    client = TestClient(create_app(settings), base_url="http://localhost")
    status = client.get("/api/status").json()
    headers = {"X-Local-Token": status["token"], "Idempotency-Key": "workspace-model"}
    assert status["llm_preference"]["choice"] == "default"
    assert status["llm_profiles"][choice] == {"provider": provider, f"{provider}_model": terra}
    if paid:
        assert client.post("/api/llm-preference", headers=headers, json={"choice": choice}).status_code == 400
    selected = {"choice": choice, "budget_usd": 3 if paid else None}
    assert client.post("/api/llm-preference", headers=headers, json=selected).json() == selected
    client = TestClient(create_app(settings), base_url="http://localhost")
    status = client.get("/api/status").json()
    headers["X-Local-Token"] = status["token"]
    assert status["llm_preference"] == selected
    layout = store.add_layout({"name": "Test"})
    response = client.post(
        "/api/runs",
        headers=headers,
        json={
            "video": "abc_def-ghI",
            "layout_id": layout,
            "provider": "gemini",
            **({"budget_usd": 1} if paid else {}),
        },
    )
    assert response.status_code == 202, response.text
    config = store.get(response.json()["id"])["config"]
    assert (config["provider"], config[f"{provider}_model"], config["budget_usd"]) == (
        provider,
        terra,
        int(paid),
    )

    # Editing an old run must override both its saved model and an individual action setting.
    old = {"provider": "codex", "budget_usd": 0}
    run = store.admit(old, "existing-run")
    models = []

    def handle(request):
        models.append(json.loads(request.content)["model"])
        host = "api.openai.com" if paid else "127.0.0.1"
        assert request.url.host == host
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"candidates":[],"feedback":"No standalone moment."}',
                            }
                        ]
                    }
                ],
                "usage": {"input_tokens": 1000, "output_tokens": 100},
            },
        )

    def propose(body, transcript, evaluator):
        return evaluator.call("rules", "speech", Proposals, "copy")

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        for reset in (False, True):
            if reset:
                assert client.post(
                    "/api/llm-preference", headers=headers, json={"choice": "default"}
                ).is_success
            Pipeline(settings, store, run).clip_proposal(
                {"body": {}},
                {},
                settings.work / "edit",
                transport,
                propose,
                analysis={"provider": provider, "budget_usd": 1},
            )
    assert models == [terra, "gpt-6-luna"]  # A model change cannot reuse the other model's response.
    usage = store.usage(run)
    assert usage["requests"][0]["model"] == terra
    if paid:
        assert usage["requests"][0]["estimated_cost_usd"] == pytest.approx(0.0015)
        assert usage["estimated_cost_usd"] == pytest.approx(0.00165)
    else:
        assert usage["estimated_cost_usd"] is None
    assert store.get(run)["config"] == old
