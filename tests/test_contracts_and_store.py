from concurrent.futures import ThreadPoolExecutor

import pytest

from vaarattu_shorts.contracts import Rect, RunRequest, video_id
from vaarattu_shorts.storage import BusyError


def test_channel_input_and_underscore_identity():
    assert video_id("https://www.youtube.com/watch?v=abc_def-ghI&list=ignored") == "abc_def-ghI"
    for value in (
        "https://evil.test/watch?v=abc_def-ghI",
        "https://youtube.com/playlist?list=123",
        "file:///abc_def-ghI",
        "https://youtube.com@evil.test/watch?v=abc_def-ghI",
    ):
        with pytest.raises(ValueError):
            video_id(value)


def test_only_turbo_and_valid_crops():
    with pytest.raises(ValueError):
        RunRequest(video="abc_def-ghI", layout_id="a" * 32, asr="large-v3")
    with pytest.raises(ValueError):
        Rect(x=0.9, y=0, width=0.2, height=0.5)
    with pytest.raises(ValueError):
        Rect(x=float("nan"), y=0, width=0.2, height=0.5)


def test_atomic_single_admission_and_idempotency(store):
    def admit(i):
        try:
            return store.admit({"video": str(i)}, str(i))
        except BusyError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(admit, range(4)))
    assert sum(r is not None for r in results) == 1
    run = store.runs()[0]
    assert store.admit(run["config"], run["request_key"]) == run["id"]
    with pytest.raises(ValueError):
        store.admit({"changed": True}, run["request_key"])


def test_pause_cancel_and_recovery_keep_slot(store):
    first = store.admit({}, "first")
    store.claim()
    store.control(first, "pause")
    assert store.get(first)["state"] == "running"
    with pytest.raises(BusyError):
        store.admit({}, "second")
    store.recover()
    assert store.get(first)["state"] == "paused"
    store.control(first, "resume")
    assert store.claim() == first
    store.control(first, "cancel")
    store.recover()
    assert store.get(first)["state"] == "cancelled"
    assert store.admit({}, "second") != first


def test_budget_reservations_are_atomic_and_ambiguous_remain_reserved(store):
    run = store.admit({}, "key")
    request = store.reserve(run, 0.3, 0.5)
    with pytest.raises(ValueError, match="Spending limit"):
        store.reserve(run, 0.3, 0.5)
    assert store.get(run)["reserved"] == 0.3
    store.settle(request, 0.1)
    store.settle(request, 0.1)
    assert store.get(run)["spent"] == 0.1
    assert store.get(run)["reserved"] == 0


def test_edit_compare_and_swap(store):
    run = store.admit({}, "one")
    store.update(run, state="completed")
    store.save_clip("clip", run, 1, {"status": "ready"})
    assert store.queue_edit("clip", 1, {"status": "pending"}) == 2
    with pytest.raises(ValueError, match="changed"):
        store.queue_edit("clip", 1, {"status": "pending"})
    assert store.get(run)["stage"] == "rerender"
