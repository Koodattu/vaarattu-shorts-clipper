import json

import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts.contracts import Proposals, RunRequest
from vaarattu_shorts.llm import Evaluator, check_provider, codex_settings
from vaarattu_shorts.web import create_app


@pytest.fixture(autouse=True)
def codex_environment(monkeypatch):
    for name in ("CODEX_BASE_URL", "CODEX_MODEL", "CODEX_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-go-to-bridge")


def response(text='{"candidates":[]}', usage=True):
    result = {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
    }
    if usage:
        result["usage"] = {
            "input_tokens": 800,
            "output_tokens": 120,
            "input_tokens_details": {"cached_tokens": 100},
            "output_tokens_details": {"reasoning_tokens": 20},
        }
    return result


@pytest.mark.parametrize("local_key", ["", "fixture-bridge-key"])
def test_codex_responses_auth_schema_reasoning_cache_and_usage(settings, store, monkeypatch, local_key):
    monkeypatch.setenv("CODEX_API_KEY", local_key)
    check_provider("codex")
    run = store.admit({"provider": "codex", "budget_usd": 0}, "bridge")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("codex", store, run, settings.work, 0, lambda: None, client)
        for _ in range(2):
            assert (
                evaluator.call(
                    "rules", "Finnish speech", Proposals, "scan", reasoning_effort="low"
                ).candidates
                == []
            )
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "http://127.0.0.1:18080/v1/responses"
    assert request.headers.get("authorization") == (f"Bearer {local_key}" if local_key else None)
    body = json.loads(request.content)
    assert body["model"] == "gpt-5.6-luna" and body["reasoning"] == {"effort": "low"}
    assert not body["stream"] and not body["store"]
    assert "max_output_tokens" not in body and "service_tier" not in body
    assert body["text"]["format"]["schema"] == Proposals.model_json_schema()
    report = store.usage(run)
    assert report["pricing_basis"] == "codex_subscription"
    assert report["estimated_cost_usd"] is report["reserved_usd"] is report["budget_usd"] is None
    assert report["input_tokens"] == 800 and report["output_tokens"] == 120
    assert report["total_tokens"] == 920
    assert report["reasoning_tokens"] == 20 and report["cached_input_tokens"] == 100
    entry = report["requests"][0]
    assert entry["reasoning_effort"] == "low" and entry["status"] == "settled"
    assert entry["estimated_cost_usd"] is entry["input_usd_per_million"] is None
    assert entry["output_token_limit_enforced"] is False
    for artifact in settings.work.glob("*.json"):
        saved = artifact.read_text("utf-8")
        assert "must-not-go-to-bridge" not in saved and "fixture-bridge-key" not in saved


def test_codex_repairs_bad_content_and_preserves_missing_usage(settings, store):
    run = store.admit({"provider": "codex"}, "missing-usage")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200, json=response("bad" if len(requests) == 1 else '{"candidates":[]}', usage=False)
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("codex", store, run, settings.work, 0, lambda: None, client)
        evaluator.call("rules", "speech", Proposals, "scan", reasoning_effort="low")
    report = store.usage(run)
    assert len(requests) == report["unreported_requests"] == 2
    assert report["reported_counts"]["input_tokens"] == 0
    assert report["estimated_cost_usd"] is None
    assert store.get(run)["spent"] == store.get(run)["reserved"] == 0


@pytest.mark.parametrize("status", [401, 429, 503])
def test_codex_access_errors_do_not_retry_or_fall_back_to_paid_api(settings, store, status):
    run = store.admit({"provider": "codex"}, "http-error")
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(status, json={"error": "fixture"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("codex", store, run, settings.work, 0, lambda: None, client)
        with pytest.raises(ValueError, match=f"HTTP {status}"):
            evaluator.call("rules", "speech", Proposals, "scan")
    assert calls == ["http://127.0.0.1:18080/v1/responses"]


def test_codex_snapshot_pins_model_endpoint_and_cache_identity(settings, store, monkeypatch):
    saved = codex_settings()
    monkeypatch.setenv("CODEX_MODEL", "fixture-model")
    monkeypatch.setenv("CODEX_BASE_URL", "http://localhost:19090/v1/")
    changed = codex_settings()
    assert changed["base_url"] == "http://localhost:19090/v1"
    run = store.admit({"provider": "codex"}, "snapshot")
    calls = []

    def handle(request):
        calls.append((str(request.url), json.loads(request.content)["model"]))
        return httpx.Response(200, json=response())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        for config in [saved, changed, saved]:
            evaluator = Evaluator(
                "codex", store, run, settings.work, 0, lambda: None, client, codex_config=config
            )
            evaluator.call("rules", "speech", Proposals, "scan")
    assert calls == [
        ("http://127.0.0.1:18080/v1/responses", "gpt-5.6-luna"),
        ("http://localhost:19090/v1/responses", "fixture-model"),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1",
        "http://127.0.0.1:18080/v1?key=secret",
        "http://user:secret@localhost:18080/v1",
        "http://127.0.0.1:99999/v1",
        "file:///v1",
        "http://127.0.0.1:18080/v1/responses",
    ],
)
def test_codex_base_url_rejects_credentials_and_nonlocal_endpoints(monkeypatch, url):
    monkeypatch.setenv("CODEX_BASE_URL", url)
    with pytest.raises(ValueError, match="CODEX_BASE_URL") as error:
        codex_settings()
    assert "secret" not in str(error.value)


def test_codex_admission_needs_no_platform_key_and_snapshots_no_secrets(settings, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("CODEX_API_KEY", "fixture-private-local-key")
    monkeypatch.setattr("vaarattu_shorts.web.preflight", lambda *_: {})
    app = create_app(settings)
    layout = app.state.store.add_layout({})
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        status = client.get("/api/status")
        assert status.json()["providers"]["codex"]["configured"]
        assert not status.json()["providers"]["openai"]["configured"]
        assert "fixture-private-local-key" not in status.text
        headers = {"X-Local-Token": status.json()["token"], "Idempotency-Key": "codex-new-run"}
        result = client.post(
            "/api/runs",
            json={"video": "abc_def-ghI", "provider": "codex", "layout_id": layout, "budget_usd": 9},
            headers=headers,
        )
        assert result.status_code == 202
        run = client.get("/api/runs/" + result.json()["id"]).json()
        assert run["config"]["codex"] == codex_settings()
        assert run["config"]["budget_usd"] == 0 and "fixture-private-local-key" not in json.dumps(run)
        assert run["usage"]["estimated_cost_usd"] is None
    with pytest.raises(ValueError, match="spending limit"):
        RunRequest(video="abc_def-ghI", provider="openai", layout_id=layout)
