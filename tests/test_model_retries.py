import json
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest

from vaarattu_shorts import llm
from vaarattu_shorts.contracts import Proposals
from vaarattu_shorts.llm import Evaluator
from vaarattu_shorts.processes import Interrupted


def success(text='{"candidates":[],"feedback":"No standalone point."}'):
    return httpx.Response(200, json={
        "status": "completed",
        "output": [{"content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 100, "output_tokens": 10},
    })


@pytest.fixture
def waits(monkeypatch):
    delays = []
    monkeypatch.setattr(Evaluator, "wait_for_retry", lambda self, reason, delay, retry: delays.append(delay))
    return delays


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_failure_recovers_same_request_and_caches(settings, store, waits, status):
    run = store.admit({"provider": "codex"}, "retry")
    requests = []

    def handle(request):
        requests.append((str(request.url), json.loads(request.content)))
        return httpx.Response(status, text="private error details") if len(requests) == 1 else success()

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("codex", store, run, settings.work, 0, lambda: None, client)
        assert evaluator.call("rules", "speech", Proposals, "scan").candidates == []
        evaluator.call("rules", "speech", Proposals, "scan")
    assert waits == [30]
    assert len(requests) == 2 and requests[0] == requests[1]
    usage = store.usage(run)["requests"]
    assert usage[0]["http_status"] == status and usage[0]["retry_delay_seconds"] == 30
    assert usage[1]["status"] == "settled"
    assert "private error details" not in json.dumps(usage)


def test_persistent_failure_has_bounded_exponential_waits(settings, store, waits):
    run = store.admit({"provider": "codex"}, "retry")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(502))) as client:
        evaluator = Evaluator("codex", store, run, settings.work, 0, lambda: None, client)
        with pytest.raises(ValueError, match="HTTP 502.*Automatic retries exhausted"):
            evaluator.call("rules", "speech", Proposals, "scan")
    assert waits == [30, 60, 120, 240]
    assert store.usage(run)["request_count"] == 5


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_transport_failure_recovers(settings, store, waits, error):
    run = store.admit({"provider": "codex"}, "transport")
    calls = []

    def handle(request):
        calls.append(request.content)
        if len(calls) == 1:
            raise error("private connection details")
        return success()

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator("codex", store, run, settings.work, 0, lambda: None, client)
        evaluator.call("rules", "speech", Proposals, "scan")
    assert waits == [30]
    assert len(calls) == 2 and calls[0] == calls[1]
    usage = store.usage(run)
    assert usage["requests"][0]["transport_error"] == error.__name__
    assert "private connection details" not in json.dumps(usage)


def test_watchdog_does_not_start_overlapping_requests(settings, store, waits, monkeypatch):
    run = store.admit({"provider": "codex"}, "watchdog")

    def post(*args):
        raise ValueError("The model request exceeded its time limit. Completed work is saved.")

    evaluator = Evaluator("codex", store, run, settings.work, 0, lambda: None)
    monkeypatch.setattr(evaluator, "post", post)
    with pytest.raises(ValueError, match="exceeded its time limit"):
        evaluator.call("rules", "speech", Proposals, "scan")
    assert waits == []
    assert store.usage(run)["request_count"] == 1


@pytest.mark.parametrize("header,expected", [("75", 75), ("invalid", 30), ("-1", 30), ("nan", 30), ("date", 80)])
def test_retry_after_is_respected(settings, store, waits, monkeypatch, header, expected):
    monkeypatch.setattr(llm, "time", SimpleNamespace(time=lambda: 1000, monotonic=lambda: 0))
    if header == "date":
        header = format_datetime(datetime.fromtimestamp(1080, timezone.utc), usegmt=True)
    run = store.admit({"provider": "codex"}, "retry-after")
    responses = iter([httpx.Response(429, headers={"Retry-After": header}), success()])
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))) as client:
        evaluator = Evaluator("codex", store, run, settings.work, 0, lambda: None, client)
        evaluator.call("rules", "speech", Proposals, "scan")
    assert waits == [expected]


def test_long_server_wait_does_not_retry_early(settings, store, waits):
    run = store.admit({"provider": "codex"}, "long-wait")
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(429, headers={"Retry-After": "3600"})
    )) as client:
        evaluator = Evaluator("codex", store, run, settings.work, 0, lambda: None, client)
        with pytest.raises(ValueError, match="longer than 15 minutes"):
            evaluator.call("rules", "speech", Proposals, "scan")
    assert waits == []
    assert store.usage(run)["request_count"] == 1


def test_service_retry_does_not_consume_json_repair(settings, store, waits):
    run = store.admit({"provider": "codex"}, "repair")
    responses = iter([httpx.Response(502), success("invalid"), httpx.Response(503), success()])
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))) as client:
        evaluator = Evaluator("codex", store, run, settings.work, 0, lambda: None, client)
        evaluator.call("rules", "speech", Proposals, "scan")
    assert waits == [30, 60]
    assert [r["attempt"] for r in store.usage(run)["requests"]] == [1, 1, 2, 2]


def test_retry_respects_spend_cap(settings, store, waits, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    run = store.admit({}, "budget")
    evaluator = Evaluator("deepseek", store, run, settings.work, 1, lambda: None)
    size = evaluator.request_size("rules", "speech", Proposals)
    input_rate, output_rate = llm.rates("deepseek")
    reserve = (size * input_rate + llm.MAX_OUTPUT * output_rate) / 1e6
    evaluator.cap = reserve * 1.5
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(502))) as client:
        evaluator.client = client
        with pytest.raises(ValueError, match="Spending limit reached"):
            evaluator.call("rules", "speech", Proposals, "scan")
    assert store.usage(run)["request_count"] == 1
    assert store.get(run)["reserved"] == pytest.approx(reserve)


@pytest.mark.parametrize("cancel", [False, True])
def test_wait_shows_countdown_and_remains_interruptible(settings, store, monkeypatch, cancel):
    run = store.admit({"provider": "codex"}, "countdown")
    store.update(run, message="Checking clip 5 of 20.")
    clock = [0.0]
    messages = []

    def sleep(seconds):
        assert seconds <= 0.2
        clock[0] += seconds

    def check():
        if cancel and clock[0] >= 0.4:
            raise Interrupted("pause")

    monkeypatch.setattr(llm, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    evaluator = Evaluator("codex", store, run, settings.work, 0, check)
    monkeypatch.setattr(evaluator, "report", lambda message: messages.append(message))
    if cancel:
        with pytest.raises(Interrupted):
            evaluator.wait_for_retry("HTTP 502.", 30, 1)
        assert clock[0] < 1
    else:
        evaluator.wait_for_retry("HTTP 502.", 30, 1)
        assert clock[0] == pytest.approx(30)
        assert "30 seconds (retry 1 of 4)" in messages[0]
        assert any("25 seconds" in message for message in messages)
        assert len(messages) <= 7
        assert messages[-1] == "Checking clip 5 of 20."
