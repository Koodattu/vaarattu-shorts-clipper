import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import worker
from vaarattu_shorts.processes import Interrupted, ToolError, waiting_lock
from vaarattu_shorts.storage import BusyError, Store
from vaarattu_shorts.web import create_app


def test_parallel_claims_never_exceed_limit_and_lowering_drains(store):
    for i in range(8):
        store.admit({}, str(i))
    store.set_concurrency(3)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = [r for r in pool.map(lambda _: store.claim(), range(8)) if r]
    assert len(claimed) == 3
    store.set_concurrency(1)
    for run in claimed[:2]:
        store.update(run, state="completed")
        assert store.claim() is None
    store.update(claimed[2], state="completed")
    assert store.claim() is not None
    assert Store(store.path).concurrency() == 1


def test_migrates_single_active_index_without_changing_jobs(store):
    first = store.admit({"video": "saved"}, "old")
    with store.connect() as db:
        db.execute("CREATE UNIQUE INDEX one_active ON runs((1)) WHERE state IN ('queued','running','paused')")
    reopened = Store(store.path)
    assert reopened.admit({}, "new") != first
    assert reopened.get(first)["config"] == {"video": "saved"}


def test_other_runs_do_not_block_edits_rechecks_or_resume(store):
    active = store.admit({}, "active")
    store.claim()
    other = store.admit({}, "other")
    store.control(other, "cancel")
    store.control(other, "resume")
    store.update(other, state="completed")
    store.control(other, "recheck")
    store.update(other, state="completed")
    store.save_clip("clip", other, 1, {"status": "ready"})
    assert store.queue_edit("clip", 1, {"status": "pending"}) == 2
    with pytest.raises(BusyError):
        store.queue_edit("clip", 2, {"status": "pending"})
    assert store.get(active)["state"] == "running"


def test_concurrency_endpoint_is_guarded_bounded_and_persistent(settings):
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        status = client.get("/api/status").json()
        assert status["max_concurrent_jobs"] == 1
        headers = {"X-Local-Token": status["token"]}
        assert client.post("/api/concurrency", json={"max_concurrent_jobs": 2}).status_code == 403
        for value in [0, 5]:
            assert (
                client.post(
                    "/api/concurrency", json={"max_concurrent_jobs": value}, headers=headers
                ).status_code
                == 422
            )
        assert (
            client.post("/api/concurrency", json={"max_concurrent_jobs": 2}, headers=headers).status_code
            == 200
        )
    assert Store(settings.work / "state.sqlite3").concurrency() == 2


@pytest.mark.parametrize("failure", ["pause", "error"])
def test_worker_runs_two_jobs_at_once_and_isolates_interruptions(settings, store, monkeypatch, failure):
    runs = [store.admit({}, str(i)) for i in range(2)]
    store.set_concurrency(2)
    barrier = threading.Barrier(2, timeout=3)
    mutex = threading.Lock()
    active, maximum, finished = 0, 0, 0
    stop = []

    class FixturePipeline:
        def __init__(self, settings, store, run_id, stopping):
            self.run_id = run_id

        def execute(self):
            nonlocal active, maximum, finished
            with mutex:
                active += 1
                maximum = max(maximum, active)
            try:
                barrier.wait()
                if self.run_id == runs[0]:
                    raise Interrupted() if failure == "pause" else ToolError("Fixture failure")
                store.update(self.run_id, state="completed")
            finally:
                with mutex:
                    active -= 1
                    finished += 1
                    if finished == 2:
                        stop[0]()

    monkeypatch.setattr(worker, "Pipeline", FixturePipeline)
    monkeypatch.setattr(worker, "lock", lambda _: nullcontext())
    monkeypatch.setattr(worker.signal, "signal", lambda _, handler: stop.append(handler))
    worker.work(settings)
    assert maximum == 2
    assert store.get(runs[0])["state"] == ("paused" if failure == "pause" else "failed")
    assert store.get(runs[1])["state"] == "completed"


def test_gpu_wait_is_exclusive_and_cancellable(tmp_path):
    path = tmp_path / "gpu.lock"
    name = "Local\\VaarattuShortsTest" + uuid.uuid4().hex
    held, waiting, release = threading.Event(), threading.Event(), threading.Event()

    def owner():
        with waiting_lock(path, name, lambda: None):
            held.set()
            assert release.wait(3)

    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks > 1:
            waiting.set()
            raise Interrupted()

    def blocked():
        with pytest.raises(Interrupted):
            with waiting_lock(path, name, check):
                pytest.fail("GPU acquired while another job owns it")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(owner)
        try:
            assert held.wait(3)
            second = pool.submit(blocked)
            assert waiting.wait(3)
            second.result(timeout=3)
        finally:
            release.set()
        first.result(timeout=3)
    with waiting_lock(path, name, lambda: None):
        pass
