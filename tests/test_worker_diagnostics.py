import json
from contextlib import nullcontext

from vaarattu_shorts import worker


def test_unexpected_failure_keeps_recovery_intent_and_saves_safe_locations(settings, store, monkeypatch):
    run = store.admit({}, "diagnostic")
    store.update(run, stage="render", result={"selection_recheck_requested": True})

    class FailedPipeline:
        def __init__(self, *_):
            pass

        def recheck_selection(self):
            raise RuntimeError("SECRET_FIXTURE https://example.invalid/?token=PRIVATE_FIXTURE")

    monkeypatch.setattr(worker, "Pipeline", FailedPipeline)
    monkeypatch.setattr(worker, "Store", lambda _: store)
    monkeypatch.setattr(worker, "lock", lambda _: nullcontext())
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    worker.work(settings, once=True)
    saved = store.get(run)
    assert saved["state"] == "failed" and saved["result"]["selection_recheck_requested"]
    report = (settings.work / "runs" / run / "worker-error.json").read_text("utf-8")
    parsed = json.loads(report)
    assert parsed["exception_type"] == "RuntimeError" and parsed["stage"] == "render"
    assert parsed["frames"][-1]["function"] == "recheck_selection"
    assert all(set(frame) == {"file", "function", "line"} for frame in parsed["frames"])
    assert "SECRET_FIXTURE" not in report + saved["message"]
    assert "PRIVATE_FIXTURE" not in report + saved["message"]
    assert "worker-error.json" in saved["message"]
