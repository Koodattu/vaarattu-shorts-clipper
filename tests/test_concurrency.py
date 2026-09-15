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


@pytest.mark.parametrize("flag", [
    "caption_check_requested", "tighten_requested", "context_repair_requested", "rerender_requested",
])
def test_review_claim_bypasses_vod_limit_and_survives_recovery(store, flag):
    vod = store.admit({}, "vod")
    assert store.claim(review=False) == vod
    other_vod = store.admit({}, "waiting-vod")
    review = store.admit({}, "review")
    store.update(review, result={flag: "clip-id"}, stage="render")
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim(review=True), range(4)))
    assert claims.count(review) == 1
    assert store.claim(review=False) is None
    store.update(vod, state="completed")
    assert store.claim(review=False) == other_vod
    store.recover()
    assert store.claim(review=True) == review
    assert store.get(other_vod)["state"] == "queued"


def test_main_pipeline_caption_stage_is_not_interactive(store):
    run = store.admit({}, "ordinary-caption-pass")
    store.update(run, stage="caption-check", result={"caption_check_requested": False})
    assert store.claim(review=True) is None
    assert store.claim(review=False) == run


def test_review_workers_do_not_wait_for_either_pool(settings, store, monkeypatch):
    vods = [store.admit({}, f"vod-{i}") for i in range(4)]
    store.set_concurrency(4)
    barrier = threading.Barrier(9, timeout=5)
    mutex = threading.Lock()
    stop = []
    finished = 0
    reviews = []

    class FixturePipeline:
        def __init__(self, settings, store, run_id, stopping):
            self.run_id = run_id

        def complete(self):
            nonlocal finished
            try:
                barrier.wait()
                if self.run_id == reviews[0]:
                    raise ToolError("Review failed independently.")
                store.update(self.run_id, state="completed", result={})
            finally:
                with mutex:
                    finished += 1
                    if finished == 9:
                        stop[0]()

        def execute(self):
            # Submit reviews only once all four VOD slots are occupied.
            with mutex:
                if not reviews:
                    for i, flag in enumerate([
                        "caption_check_requested", "tighten_requested", "context_repair_requested",
                        "rerender_requested", "caption_check_requested",
                    ]):
                        run = store.admit({}, f"review-{i}")
                        store.update(run, result={flag: "clip-id"})
                        reviews.append(run)
            self.complete()

        check_captions = complete
        suggest_tighter_edit = complete
        repair_context = complete
        rerender = complete

    monkeypatch.setattr(worker, "Pipeline", FixturePipeline)
    monkeypatch.setattr(worker, "lock", lambda _: nullcontext())
    monkeypatch.setattr(worker.signal, "signal", lambda _, handler: stop.append(handler))
    worker.work(settings)
    assert finished == 9
    assert store.get(reviews[0])["state"] == "failed"
    assert all(store.get(run)["state"] == "completed" for run in vods + reviews[1:])
