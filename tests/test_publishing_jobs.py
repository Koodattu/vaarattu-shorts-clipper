import threading

import pytest

from vaarattu_shorts.publishing_jobs import FillJob, FillStopped
from vaarattu_shorts.processes import lock
from vaarattu_shorts.storage import atomic_json


OPTIONS = {"provider": "openai", "budget_usd": 5}


def test_stop_is_visible_and_does_not_allow_stale_job_ids(settings):
    job = FillJob(settings)
    ready, resume = threading.Event(), threading.Event()
    failures = []

    def work(**options):
        assert options["analysis"] == OPTIONS
        options["progress"]("Writing clip 1 of 2", stage="copy", current=1, total=2)
        ready.set()
        assert resume.wait(5)
        options["check"]()
        pytest.fail("Stopped work must not reach submission")

    def run():
        try:
            with lock(job.publishing_lock):
                job.run(work, OPTIONS)
        except FillStopped as exc:
            failures.append(str(exc))

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert ready.wait(5)
        state = job.status()
        assert state["state"] == "running" and state["stage"] == "copy"
        with pytest.raises(ValueError, match="no longer running"):
            job.stop("previous-job")
        assert job.stop(state["id"])["stopping"]
    finally:
        resume.set()
        worker.join(5)
    assert not worker.is_alive()
    assert failures and job.status()["state"] == "stopped"
    assert "Retry" in job.status()["message"]
    with pytest.raises(ValueError):
        job.stop(state["id"])


def test_interrupted_fill_is_recoverable_only_after_publishing_lock_released(settings):
    job = FillJob(settings)
    atomic_json(job.path, {"id": "old", "state": "running", "scheduled": 1})
    with lock(job.publishing_lock):
        assert job.status()["state"] == "running"
    assert job.status()["state"] == "interrupted"
    assert job.status()["scheduled"] == 1
    assert "Retry" in job.status()["message"]


def test_failure_and_success_survive_new_controller(settings):
    job = FillJob(settings)

    def failed(**options):
        options["progress"]("Submitting", scheduled=1)
        raise ValueError("Model unavailable")

    with pytest.raises(ValueError, match="Model unavailable"):
        job.run(failed, OPTIONS)
    saved = FillJob(settings).status()
    assert saved["state"] == "failed" and saved["scheduled"] == 1
    assert saved["message"] == "Model unavailable"
    job.run(lambda **options: {"scheduled": 2, "message": ""}, OPTIONS)
    assert FillJob(settings).status()["result"]["scheduled"] == 2


def test_fill_has_total_deadline(settings, monkeypatch):
    from vaarattu_shorts import publishing_jobs
    now = [0]
    monkeypatch.setattr(publishing_jobs.time, "monotonic", lambda: now[0])
    job = FillJob(settings)

    def work(**options):
        now[0] = 1800
        options["check"]()

    with pytest.raises(ValueError, match="30-minute"):
        job.run(work, OPTIONS)
    assert job.status()["state"] == "failed"
