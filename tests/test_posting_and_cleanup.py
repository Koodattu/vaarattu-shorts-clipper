from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import delivery
from vaarattu_shorts.storage import digest
from vaarattu_shorts.web import create_app


def rendered(settings, store, run, key, start, decision="ready_to_post"):
    folder = settings.ready / key / "1"
    folder.mkdir(parents=True)
    final = folder / "short.mp4"
    final.write_bytes(f"final-{key}".encode())
    (folder / "captions.srt").write_text("saved captions")
    body = dict(
        title=key,
        source_title="Recording",
        start_us=start,
        end_us=start + 5000000,
        words=[],
        status="ready",
        folder=str(folder),
        video_sha256=digest(final),
    )
    store.save_clip(key, run, 1, body)
    store.review_clip(key, 1, decision)
    return body


@pytest.fixture
def completed(settings, store):
    run = store.admit({"video": "source"}, "delivery")
    store.update(run, state="completed")
    rendered(settings, store, run, "late", 30000000)
    rendered(settings, store, run, "early", 10000000)
    rendered(settings, store, run, "rejected", 20000000, "not_approved")
    folder = settings.work / "runs" / run
    folder.mkdir(parents=True)
    (folder / "source.opus").write_bytes(b"source")
    (folder / "transcript.json").write_text('{"words":[]}')
    return run


def test_ready_stamp_is_revision_specific_but_approval_survives_edits(settings, store, completed):
    clip = store.clip("early")
    assert clip["review_status"] == "ready_to_post"
    body = {**clip["body"], "status": "pending", "folder": None}
    assert store.queue_edit("early", 1, body) == 2
    assert store.clip("early")["review_status"] == "approved"
    with pytest.raises(ValueError):
        store.review_clip("early", 2, "ready_to_post")
    store.save_clip("early", completed, 2, clip["body"])
    with pytest.raises(ValueError, match="Finish this recording"):
        store.review_clip("early", 2, "ready_to_post")
    store.update(completed, state="completed")
    store.review_clip("early", 2, "ready_to_post")
    assert store.clip("early")["review_status"] == "ready_to_post"


def test_schedule_groups_source_order_and_keeps_independent_platform_receipts(settings, store, completed):
    other = store.admit({"video": "other"}, "other")
    store.update(other, state="completed")
    rendered(settings, store, other, "other-first", 0)
    result = delivery.schedule(store, [completed, other], "2030-10-25", "18:00", "Europe/Helsinki")
    assert result == {"added": 3}
    plan = delivery.posting_plan(store)
    assert [p["clip_id"] for p in plan] == ["early", "late", "other-first"]
    assert [p["local_date"] for p in plan] == ["2030-10-25", "2030-10-26", "2030-10-27"]
    assert plan[0]["scheduled_at"].startswith("2030-10-25T15:00")
    assert plan[2]["scheduled_at"].startswith("2030-10-27T16:00"), "Keep local daily time through DST"
    assert all(p["ready"] for p in plan)
    with pytest.raises(ValueError, match="unscheduled"):
        delivery.schedule(store, [completed], "2030-10-25", "18:00", "Europe/Helsinki")
    delivery.record_post(store, plan[0]["id"], "youtube", "https://www.youtube.com/shorts/example")
    posted = delivery.posting_plan(store)[0]
    assert posted["deliveries"]["youtube"]["status"] == "posted"
    assert posted["deliveries"]["instagram"]["status"] == "pending"
    with pytest.raises(ValueError):
        delivery.unschedule(store, posted["id"])
    with pytest.raises(ValueError):
        delivery.record_post(store, posted["id"], "instagram", "https://evil.example/post")
    store.review_clip("early", 1, "approved")
    assert not delivery.posting_plan(store)[0]["ready"]


def test_multiple_runs_of_one_source_are_ordered_together(settings, store, completed):
    repeated = store.admit({"video": "source"}, "repeat")
    store.update(repeated, state="completed")
    rendered(settings, store, repeated, "middle", 20000000)
    delivery.schedule(store, [completed, repeated], "2030-01-01", "11:15", "Europe/Helsinki")
    assert [p["clip_id"] for p in delivery.posting_plan(store)] == ["early", "middle", "late"]
    another = store.admit({"video": "source"}, "another")
    store.update(another, state="completed")
    rendered(settings, store, another, "earlier", 0)
    with pytest.raises(ValueError, match="preserve source order"):
        delivery.schedule(store, [another], "2030-01-05", "11:15", "Europe/Helsinki")
    assert len(delivery.posting_plan(store)) == 3


@pytest.mark.parametrize(
    "day,clock,zone",
    [
        ("2030-10-27", "03:30", "Europe/Helsinki"),
        ("2030-03-31", "03:30", "Europe/Helsinki"),
        ("2000-01-01", "18:00", "Europe/Helsinki"),
        ("2030-10-25", "bad", "Europe/Helsinki"),
    ],
)
def test_schedule_rejects_ambiguous_missing_and_past_times_atomically(store, completed, day, clock, zone):
    with pytest.raises(ValueError):
        delivery.schedule(store, [completed], day, clock, zone)
    assert delivery.posting_plan(store) == []


def test_cleanup_preview_then_delete_only_obsolete_media(settings, store, completed):
    model = settings.models / "keep.bin"
    model.write_bytes(b"shared model")
    old = settings.ready / "early" / "0"
    old.mkdir()
    (old / "short.mp4").write_bytes(b"old")
    with store.connect() as db:
        plan = delivery.cleanup_plan(settings, store, db, completed)
    assert plan["file_count"] == 3
    assert (settings.work / "runs" / completed / "source.opus").exists(), "Preview must not delete anything"
    result = delivery.cleanup(settings, store, completed, plan["fingerprint"])
    assert result["deleted_bytes"] == plan["bytes"] and result["remaining_files"] == []
    assert not (old / "short.mp4").exists()
    assert not (settings.ready / "rejected" / "1" / "short.mp4").exists()
    assert (settings.ready / "early" / "1" / "short.mp4").exists()
    assert (settings.ready / "early" / "1" / "captions.srt").exists()
    assert (settings.work / "runs" / completed / "transcript.json").exists() and model.exists()
    assert store.clip("rejected")["review_status"] == "not_approved"
    with pytest.raises(ValueError, match="cleaned"):
        store.queue_edit("early", 1, store.clip("early")["body"])
    with pytest.raises(ValueError, match="cleaned"):
        store.control(completed, "recheck")


def test_cleanup_keeps_other_active_work_and_can_recover_after_interruption(
    settings, store, completed, monkeypatch
):
    active = store.admit({"video": "other"}, "active")
    store.update(active, state="running")
    other = settings.work / "runs" / active / "source.opus"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"active recording")
    staged = settings.ready / ".staging" / "rejected" / "2" / "short.mp4"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"old held render")
    with store.connect() as db:
        plan = delivery.cleanup_plan(settings, store, db, completed)
    unlink = Path.unlink

    def interrupted(path, *args, **kwargs):
        if path.name == "source.opus":
            raise SystemExit("simulated crash")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(SystemExit):
            delivery.cleanup(settings, store, completed, plan["fingerprint"])
    with pytest.raises(ValueError, match="cleaned"):
        store.queue_edit("early", 1, store.clip("early")["body"])
    with store.connect() as db:
        remaining = delivery.cleanup_plan(settings, store, db, completed)
    assert delivery.cleanup(settings, store, completed, remaining["fingerprint"])["remaining_files"] == []
    assert other.read_bytes() == b"active recording" and store.get(active)["state"] == "running"
    assert not staged.exists()


def test_cleanup_blocks_active_work_stale_previews_and_missing_finals(settings, store, completed):
    with store.connect() as db:
        plan = delivery.cleanup_plan(settings, store, db, completed)
    store.update(completed, state="running")
    with pytest.raises(ValueError, match="processing"):
        delivery.cleanup(settings, store, completed, plan["fingerprint"])
    store.update(completed, state="completed")
    source = settings.work / "runs" / completed / "source.opus"
    source.write_bytes(b"changed media")
    with pytest.raises(ValueError, match="changed"):
        delivery.cleanup(settings, store, completed, plan["fingerprint"])
    assert source.exists()
    (settings.ready / "early" / "1" / "short.mp4").unlink()
    with store.connect() as db, pytest.raises(ValueError, match="missing"):
        delivery.cleanup_plan(settings, store, db, completed)
    assert source.exists()


def test_cleanup_rejects_outside_paths_and_linked_media(settings, store, completed, monkeypatch):
    with pytest.raises(ValueError, match="outside"):
        delivery.checked_path(settings.root / "outside.mp4", settings.work)
    source = settings.work / "runs" / completed / "source.opus"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == source or original(p))
    with store.connect() as db, pytest.raises(ValueError, match="linked"):
        delivery.cleanup_plan(settings, store, db, completed)
    assert source.exists()


def test_delivery_endpoints_require_local_token_and_recheck_ready_status(settings, store, completed):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": client.get("/api/status").json()["token"]}
    body = dict(run_ids=[completed], start_date="2030-01-01", local_time="12:00", timezone="Europe/Helsinki")
    assert client.post("/api/delivery/schedule", json=body).status_code == 403
    assert client.post("/api/delivery/schedule", json=body, headers=headers).status_code == 200
    data = client.get("/api/delivery").json()
    assert len(data["plan"]) == 2 and data["recordings"][0]["resolved"]
    store.review_clip("late", 1, "approved")
    assert client.get(f"/api/delivery/cleanup/{completed}").status_code == 400
    assert not client.get("/api/delivery").json()["recordings"][0]["resolved"]


def test_held_clip_can_be_explicitly_discarded_without_a_preview(settings, store, completed):
    store.save_clip("held", completed, 1, dict(title="Held", status="held", folder=None))
    assert not delivery.recordings(store)[0]["resolved"]
    with pytest.raises(ValueError):
        store.review_clip("held", 1, "ready_to_post")
    store.review_clip("held", 1, "not_approved")
    assert delivery.recordings(store)[0]["resolved"]


def test_completed_recording_with_no_candidates_can_reclaim_source(settings, store):
    run = store.admit({"video": "none"}, "no-clips")
    store.update(run, state="completed", result={"outcome": "no_candidates"})
    source = settings.work / "runs" / run / "source.opus"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"no clips")
    with store.connect() as db:
        plan = delivery.cleanup_plan(settings, store, db, run)
    assert plan["kept_videos"] == 0 and plan["file_count"] == 1
    delivery.cleanup(settings, store, run, plan["fingerprint"])
    assert not source.exists()
