from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import buffer, delivery, publishing_copy, publishing_queue
from vaarattu_shorts.web import create_app
from test_buffer import remote as remote
from test_posting_and_cleanup import rendered


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(publishing_copy, "_propose", lambda *args: publishing_copy.Copy(
        title="Posting title", caption="Posting caption."))
    monkeypatch.setattr(buffer, "now", lambda: datetime(2030, 10, 26, 5, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(publishing_queue.random, "randint", lambda low, high: low)


def recording(settings, store, source, published, count):
    run = store.admit({"video": source}, source)
    store.update(run, state="completed")
    with store.connect() as db:
        db.execute("INSERT INTO youtube_videos VALUES(?,?,?,?)", (source, "channel", published, "{}"))
    for n in reversed(range(count)):
        rendered(settings, store, run, f"{source}-{n:02d}", n * 10_000_000)
    return run


def outside_post(remote, n, platform="youtube", due="2030-10-25T06:00:00Z", **fields):
    remote["posts"][f"outside-{n}"] = dict(id=f"outside-{n}", channelId=platform, status="scheduled",
                                          dueAt=due, assets=[], **fields)


def test_fill_schedules_ten_then_continues_source_group_without_reposts(settings, store, remote, monkeypatch):
    offsets = iter([0, 120, 17, 53, 92, 11, 119, 64, 38, 81])
    monkeypatch.setattr(publishing_queue.random, "randint", lambda low, high: next(offsets))
    recording(settings, store, "new-video", "2030-10-01T00:00:00Z", 12)
    # Processing order differs from source age: this older recording was processed last.
    recording(settings, store, "old-video", "2020-01-01T00:00:00Z", 2)
    with store.connect() as db:
        db.execute("INSERT INTO youtube_videos VALUES('source','channel','2019-01-01T00:00:00Z','{}')")
    result = publishing_queue.fill(settings, store)
    assert result["scheduled"] == 10
    assert [c["clip_id"] for c in result["clips"]] == [f"new-video-{n:02d}" for n in range(10)]
    assert len(remote["mutations"]) == 30
    assert buffer.config(settings)["remaining"] == dict.fromkeys(buffer.PLATFORMS, 0)
    assert result["clips"][0]["due_at"] == "2030-10-26T16:00:00+00:00"
    assert result["clips"][1]["due_at"] == "2030-10-27T19:00:00+00:00", "The window stays local through DST"
    local_times = [datetime.fromisoformat(c["due_at"]).astimezone(ZoneInfo("Europe/Helsinki"))
                   for c in result["clips"]]
    assert all(time(19) <= due.time() <= time(21) for due in local_times)
    assert len({due.time() for due in local_times}) == 10
    assert all(b.date() - a.date() == timedelta(days=1) for a, b in zip(local_times, local_times[1:]))
    assert [m["dueAt"] for m in remote["mutations"]] == [c["due_at"] for c in result["clips"] for _ in range(3)]
    assert [p["scheduled_at"] for p in delivery.posting_plan(store)] == [c["due_at"] for c in result["clips"]]
    assert publishing_queue.fill(settings, store)["scheduled"] == 0
    assert len(remote["mutations"]) == 30
    for post in remote["posts"].values():
        post["status"] = "sent"
    recording(settings, store, "newest-video", "2030-10-20T00:00:00Z", 1)
    monkeypatch.setattr(publishing_queue.random, "randint", lambda low, high: low)
    second = publishing_queue.fill(settings, store)
    assert [c["clip_id"] for c in second["clips"]] == ["new-video-10", "new-video-11", "newest-video-00", "old-video-00", "old-video-01", "early", "late"]
    assert len({m["assets"][0]["video"]["url"] for m in remote["mutations"]}) == 17
    assert all(m["mode"] == "customScheduled" for m in remote["mutations"])


def test_pool_requires_final_stamp_but_not_all_other_clips_reviewed(settings, store, remote):
    store.review_clip("late", 1, "approved")
    result = publishing_queue.fill(settings, store)
    assert [c["clip_id"] for c in result["clips"]] == ["early"]
    assert store.clip("late")["review_status"] == "approved"


def test_later_newly_finished_earlier_moment_is_not_posted_out_of_order(settings, store, remote):
    store.review_clip("early", 1, "approved")
    assert publishing_queue.fill(settings, store)["clips"][0]["clip_id"] == "late"
    store.review_clip("early", 1, "ready_to_post")
    result = publishing_queue.fill(settings, store)
    assert result["scheduled"] == 0 and result["skipped_order"] == 1


def test_manual_posted_and_edited_clips_are_not_reposted(settings, store, remote):
    delivery.schedule(store, [remote["run"]], "2030-10-26", "18:00", "Europe/Helsinki")
    early = delivery.posting_plan(store)[0]
    delivery.record_post(store, early["id"], "youtube", "https://youtube.com/shorts/posted")
    result = publishing_queue.fill(settings, store)
    assert [c["clip_id"] for c in result["clips"]] == ["late"]
    assert len(remote["mutations"]) == 3
    # Same clip identity remains excluded after another revision is marked final.
    old = store.clip("late")
    store.save_clip("late", remote["run"], 2, old["body"])
    store.review_clip("late", 2, "ready_to_post")
    assert publishing_queue.fill(settings, store)["scheduled"] == 0


def test_queue_uses_smallest_available_capacity_and_appends_after_external_posts(settings, store, remote):
    for n in range(9):
        outside_post(remote, n, due="2030-10-30T19:00:00Z")
    result = publishing_queue.fill(settings, store)
    assert result["scheduled"] == 1
    assert result["clips"][0]["due_at"] == "2030-10-31T17:00:00+00:00"
    assert len(remote["mutations"]) == 3


def test_matching_remote_video_and_repeated_source_moment_are_excluded(settings, store, remote):
    early = store.clip("early")
    outside_post(remote, 0)
    remote["posts"]["outside-0"]["assets"] = [{"source": f"{publishing_queue.r2.PUBLIC_URL}/clips/{early['body']['video_sha256']}.mp4"}]
    repeat = store.admit({"video": "source"}, "repeat")
    store.update(repeat, state="completed")
    rendered(settings, store, repeat, "early-copy", early["body"]["start_us"])
    rendered(settings, store, repeat, "late-copy", 30_000_000)
    result = publishing_queue.fill(settings, store)
    assert result["scheduled"] == 1
    assert result["clips"][0]["clip_id"] in {"late", "late-copy"}


def test_partial_submission_stops_fill_until_resolved(settings, store, remote):
    remote["fail_channel"] = "instagram"
    result = publishing_queue.fill(settings, store)
    assert result["scheduled"] == 0 and "attention" in result["message"]
    assert len(remote["mutations"]) == 2
    with pytest.raises(ValueError, match="incomplete"):
        publishing_queue.fill(settings, store)
    assert len(remote["mutations"]) == 2
    assert "late" not in buffer.publications(store)


def test_selected_pending_local_plan_is_retimed_but_remote_schedule_is_kept(settings, store, remote):
    delivery.schedule(store, [remote["run"]], "2030-11-15", "18:00", "Europe/Helsinki")
    outside_post(remote, 0, due="2030-10-29T14:00:00Z")
    result = publishing_queue.fill(settings, store)
    assert [c["due_at"] for c in result["clips"]] == ["2030-10-30T17:00:00+00:00", "2030-10-31T17:00:00+00:00"]
    assert remote["posts"]["outside-0"]["dueAt"] == "2030-10-29T14:00:00Z"
    assert all(p["buffer_managed"] for p in delivery.posting_plan(store))


def test_after_window_starts_tomorrow_and_skips_existing_reserved_day(settings, store, remote, monkeypatch):
    monkeypatch.setattr(buffer, "now", lambda: datetime(2030, 10, 26, 19, 0, tzinfo=timezone.utc))
    outside_post(remote, 0, due="2030-10-27T00:00:00Z")
    result = publishing_queue.fill(settings, store)
    assert result["clips"][0]["due_at"] == "2030-10-28T17:00:00+00:00"


@pytest.mark.parametrize(("local_now", "expected", "latest"), [
    ("2030-10-26T18:00:00", "2030-10-26T19:00:00", False),
    ("2030-10-26T19:30:20", "2030-10-26T19:41:00", False),
    ("2030-10-26T19:30:20", "2030-10-26T21:00:00", True),
    ("2030-10-26T20:49:59", "2030-10-26T21:00:00", False),
    ("2030-10-26T20:50:00", "2030-10-27T19:00:00", False),
    ("2030-10-26T23:59:00", "2030-10-27T19:00:00", False),
    ("2030-03-30T23:59:00", "2030-03-31T19:00:00", False),
])
def test_evening_window_and_lead_time(settings, store, remote, monkeypatch, local_now, expected, latest):
    zone = ZoneInfo("Europe/Helsinki")
    current = datetime.fromisoformat(local_now).replace(tzinfo=zone)
    monkeypatch.setattr(buffer, "now", lambda: current.astimezone(timezone.utc))
    monkeypatch.setattr(publishing_queue.random, "randint", lambda low, high: high if latest else low)
    result = publishing_queue.fill(settings, store)
    due = datetime.fromisoformat(result["clips"][0]["due_at"])
    assert due == datetime.fromisoformat(expected).replace(tzinfo=zone)
    assert due > current + timedelta(minutes=10)
    second = datetime.fromisoformat(result["clips"][1]["due_at"]).astimezone(zone)
    assert second.date() == due.astimezone(zone).date() + timedelta(days=1)
    assert second.time() == (time(21) if latest else time(19))


def test_bad_render_rolls_back_plan_without_any_upload(settings, store, remote):
    (settings.ready / "late" / "1" / "short.mp4").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed on disk"):
        publishing_queue.fill(settings, store)
    assert not delivery.posting_plan(store) and not remote["objects"] and not remote["mutations"]


def test_unselected_plan_dates_in_another_timezone_remain_reserved(settings, store, remote):
    delivery.schedule(store, [remote["run"]], "2030-10-26", "23:00", "America/Los_Angeles")
    store.review_clip("late", 1, "approved")
    rendered(settings, store, remote["run"], "extra", 50_000_000)
    result = publishing_queue.fill(settings, store)
    assert [c["due_at"] for c in result["clips"]] == ["2030-10-26T16:00:00+00:00", "2030-10-29T17:00:00+00:00"]


def test_fill_route_requires_local_authorization_and_shares_publishing_lock(settings, store, remote):
    from vaarattu_shorts.processes import lock

    api = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    assert api.post("/api/publishing/buffer/fill").status_code == 403
    headers = {"x-local-token": api.get("/api/status").json()["token"]}
    with lock(settings.work / "buffer-publishing.lock"):
        assert api.post("/api/publishing/buffer/fill", headers=headers).status_code == 400
    response = api.post("/api/publishing/buffer/fill", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["scheduled"] == 2
    assert api.post("/api/publishing/buffer/fill", headers=headers).json()["scheduled"] == 0
    assert len(remote["mutations"]) == 6


def test_lazy_fill_import_works_with_contracts_already_loaded_before_twitch_update(monkeypatch):
    import importlib.util
    from vaarattu_shorts import contracts

    monkeypatch.delattr(contracts, "recording_id")
    spec = importlib.util.spec_from_file_location("vaarattu_shorts._queue_import_check", publishing_queue.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.fill)
    assert module.source_key("https://youtu.be/abc_def-ghI") == "abc_def-ghI"
    assert module.source_key("https://www.twitch.tv/videos/123") == "https://www.twitch.tv/videos/123"
