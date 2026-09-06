import json

import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts.contracts import Proposals
from vaarattu_shorts.llm import Evaluator, token_usage
from vaarattu_shorts.storage import Store
from vaarattu_shorts.web import create_app


@pytest.mark.parametrize(
    "provider,raw",
    [
        (
            "openai",
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 30,
                    "input_tokens_details": {"cached_tokens": 40},
                    "output_tokens_details": {"reasoning_tokens": 20},
                }
            },
        ),
        (
            "gemini",
            {
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 10,
                    "thoughtsTokenCount": 20,
                    "totalTokenCount": 130,
                    "cachedContentTokenCount": 40,
                }
            },
        ),
        (
            "deepseek",
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 30,
                    "prompt_cache_hit_tokens": 40,
                    "completion_tokens_details": {"reasoning_tokens": 20},
                }
            },
        ),
        (
            "zai",
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 30,
                    "prompt_tokens_details": {"cached_tokens": 40},
                    "completion_tokens_details": {"reasoning_tokens": 20},
                }
            },
        ),
    ],
)
def test_usage_subsets_are_not_counted_twice(provider, raw):
    assert token_usage(provider, raw) == {
        "input_tokens": 100,
        "output_tokens": 30,
        "cached_input_tokens": 40,
        "reasoning_tokens": 20,
    }


def test_missing_usage_is_unknown_and_gemini_thoughts_count_once():
    assert all(v is None for v in token_usage("gemini", {}).values())
    assert token_usage("gemini", {"usageMetadata": {"promptTokenCount": 10}})["output_tokens"] is None
    assert (
        token_usage(
            "gemini",
            {
                "usageMetadata": {
                    "candidatesTokenCount": 5,
                    "thoughtsTokenCount": 7,
                }
            },
        )["output_tokens"]
        == 12
    )
    assert token_usage("openai", {"usage": {"input_tokens": -5}})["input_tokens"] is None


def test_truncated_response_and_repair_are_counted_and_cached(settings, store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    run = store.admit({"budget_usd": 1}, "usage")
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "incomplete" if len(calls) == 1 else "completed",
                "output": [{"content": [{"type": "output_text", "text": '{"candidates":[]}'}]}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 30,
                    "input_tokens_details": {"cached_tokens": 40},
                    "output_tokens_details": {"reasoning_tokens": 20},
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("openai", store, run, settings.work, 1, lambda: None, client)
        evaluator.call("private prompt", "transcript", Proposals, "window-1")
        evaluator.call("private prompt", "transcript", Proposals, "window-1")
    report = Store(store.path).usage(run)
    assert report["request_count"] == 2
    assert report["input_tokens"] == 200 and report["output_tokens"] == 60
    assert report["total_tokens"] == 260
    assert report["cached_input_tokens"] == 80 and report["reasoning_tokens"] == 40
    assert report["estimated_cost_usd"] == pytest.approx((200 * 0.2 + 60 * 1.2) / 1e6)
    assert report["reserved_usd"] == pytest.approx(0)
    assert report["unreported_requests"] == 0
    assert [r["attempt"] for r in report["requests"]] == [1, 2]
    assert calls[0]["service_tier"] == "default"
    assert "test-secret" not in json.dumps(report) and "private prompt" not in json.dumps(report)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.get(f"/api/runs/{run}/usage")
        assert response.json() == report
        assert "attachment" in response.headers["content-disposition"]
        assert client.get(f"/api/runs/{run}").json()["usage"]["total_tokens"] == 260


@pytest.mark.parametrize("timeout", [False, True])
def test_unknown_usage_keeps_budget_reserved(settings, store, monkeypatch, timeout):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    run = store.admit({}, "unknown")

    def handle(request):
        if timeout:
            raise httpx.ReadTimeout("test")
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"candidates":[]}'}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("deepseek", store, run, settings.work, 1, lambda: None, client)
        if timeout:
            with pytest.raises(ValueError, match="could not complete"):
                evaluator.call("system", "prompt", Proposals, "one")
        else:
            evaluator.call("system", "prompt", Proposals, "one")
    report = store.usage(run)
    assert report["unreported_requests"] == 1
    assert report["reserved_usd"] > 0
    assert report["estimated_cost_usd"] == 0
    assert report["requests"][0]["estimated_cost_usd"] is None


def test_local_usage_has_no_api_cost(settings, store, monkeypatch):
    run = store.admit({"local_model": "gemma4-31b"}, "local")
    monkeypatch.setattr(Evaluator, "input_size", lambda *args: 100)
    with httpx.Client(
        base_url="http://local",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"candidates":[]}'}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10},
                },
            )
        ),
    ) as client:
        Evaluator("local", store, run, settings.work, 0, lambda: None, client).call(
            "system", "prompt", Proposals, "one"
        )
    report = store.usage(run)
    assert report["total_tokens"] == 110
    assert report["estimated_cost_usd"] == report["reserved_usd"] == 0
    assert report["requests"][0]["model"] == "gemma4-31b"


def test_legacy_requests_survive_additive_usage_table(store):
    run = store.admit({}, "legacy")
    request = store.reserve(run, 0.1, 1)
    store.settle(request, 0.05)
    with store.connect() as db:
        db.execute("DROP TABLE request_usage")
    report = Store(store.path).usage(run)
    assert report["estimated_cost_usd"] == 0.05
    assert report["unreported_requests"] == 1
    assert report["requests"][0]["status"] == "settled"
