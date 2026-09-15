"""Final-review records, explicit media cleanup and a local daily posting plan."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .storage import REVIEW_STATUS_SQL, atomic_json, digest

PLATFORMS = ("youtube", "instagram", "tiktok")
MEDIA = {".mp4", ".mkv", ".webm", ".mov", ".opus", ".wav", ".mp3", ".m4a", ".flac", ".aac", ".ts"}


def recording_rows(store, db):
    rows = []
    for raw in db.execute("SELECT * FROM runs ORDER BY created DESC"):
        run = store.unpack(raw)
        clips = [
            store.unpack(c)
            for c in db.execute(
                f"SELECT clips.*, {REVIEW_STATUS_SQL} AS review_status "
                "FROM clips LEFT JOIN clip_reviews ON clip_reviews.clip_id=clips.id WHERE run_id=?",
                (run["id"],),
            )
        ]
        ready = [c for c in clips if c["review_status"] == "ready_to_post"]
        unresolved = [c for c in clips if c["review_status"] not in {"ready_to_post", "not_approved"}]
        rows.append(
            {
                "id": run["id"],
                "title": next(
                    (c["body"].get("source_title") for c in clips if c["body"].get("source_title")),
                    run["config"].get("video", run["id"]),
                ),
                "state": run["state"],
                "source_video": run["config"].get("video", run["id"]),
                "total": len(clips),
                "ready": len(ready),
                "unresolved": len(unresolved),
                "resolved": (bool(clips) or run["result"].get("outcome") == "no_candidates")
                and not unresolved
                and run["state"] == "completed",
                "held": [
                    {"id": c["id"], "revision": c["revision"], "title": c["body"]["title"]}
                    for c in unresolved
                    if c["body"]["status"] == "held"
                ],
                "media_cleaned": store.media_cleaned(run),
                "clips": clips,
            }
        )
    return rows


def recordings(store):
    with store.connect() as db:
        return [{k: v for k, v in row.items() if k != "clips"} for row in recording_rows(store, db)]


def checked_path(path, root):
    path, root = Path(path).absolute(), Path(root).absolute()
    if not path.is_relative_to(root) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Cleanup found a path outside this recording's workspace.")
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            if part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) & 1024:
                raise ValueError("Cleanup cannot follow linked files or folders.")
        if part == root:
            break
    return path


def media_files(folder, root):
    checked_path(folder, root)
    if not folder.exists():
        return
    for path in folder.iterdir():
        checked_path(path, root)
        if path.is_dir():
            yield from media_files(path, root)
        elif path.suffix.lower() in MEDIA or path.name.endswith((".part", ".ytdl")):
            yield path


def cleanup_plan(settings, store, db, run_id):
    row = next((r for r in recording_rows(store, db) if r["id"] == run_id), None)
    if row is None:
        raise KeyError(run_id)
    if not row["resolved"]:
        raise ValueError("Finish the run and mark every clip Not approved or Ready for posting first.")
    keep, folders = set(), [settings.work / "runs" / run_id]
    for clip in row["clips"]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", clip["id"]):
            raise ValueError("This clip's folder cannot be safely cleaned up.")
        folder = settings.ready / clip["id"]
        folders.extend([folder, settings.ready / ".staging" / clip["id"]])
        if clip["review_status"] == "ready_to_post":
            final = checked_path(folder / str(clip["revision"]) / "short.mp4", settings.work)
            if (
                not final.is_file()
                or final.stat().st_size == 0
                or Path(clip["body"].get("folder", "")).resolve() != final.parent.resolve()
            ):
                raise ValueError("A final video is missing. Restore it before cleaning up this recording.")
            keep.add(final)
        # An earlier delivered/scheduled revision must also remain downloadable.
        for post in db.execute("SELECT revision FROM posting_plan WHERE clip_id=?", (clip["id"],)):
            keep.add(checked_path(folder / str(post["revision"]) / "short.mp4", settings.work))
        for post in db.execute("SELECT body FROM buffer_publications WHERE clip_id=?", (clip["id"],)):
            keep.add(checked_path(folder / str(json.loads(post["body"])["revision"]) / "short.mp4", settings.work))
    files = sorted({p for folder in folders for p in media_files(folder, settings.work) if p not in keep})
    manifest = [[str(p.relative_to(settings.work)), p.stat().st_size, p.stat().st_mtime_ns] for p in files]
    identity = [
        [c["id"], c["revision"], c["review_status"], c["body"].get("video_sha256")] for c in row["clips"]
    ]
    fingerprint = hashlib.sha256(
        json.dumps([identity, manifest, sorted(map(str, keep))]).encode()
    ).hexdigest()
    return {
        "run_id": run_id,
        "title": row["title"],
        "fingerprint": fingerprint,
        "bytes": sum(f[1] for f in manifest),
        "file_count": len(files),
        "kept_videos": len(keep),
        "files": manifest,
        "clips": row["clips"],
    }


def cleanup(settings, store, run_id, fingerprint):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
            "SELECT 1 FROM runs WHERE id=? AND state IN ('queued','running','paused')", (run_id,)
        ).fetchone():
            raise ValueError(
                "Wait until this recording's processing has finished before deleting its source media."
            )
        plan = cleanup_plan(settings, store, db, run_id)
        if plan["fingerprint"] != fingerprint:
            raise ValueError("The cleanup contents changed. Preview the cleanup again.")
        for clip in plan["clips"]:
            if clip["review_status"] == "ready_to_post" and clip["body"].get("video_sha256"):
                final = settings.ready / clip["id"] / str(clip["revision"]) / "short.mp4"
                if digest(final) != clip["body"]["video_sha256"]:
                    raise ValueError(
                        "A final video changed on disk. Check it before deleting the source media."
                    )
        deleted, errors = 0, []
        # Filesystem deletion cannot roll back with SQLite. Persist intent first so a crash
        # cannot allow source edits against a partially deleted recording.
        atomic_json(
            settings.work / "runs" / run_id / "media-cleanup.json",
            {"started": time.time(), "fingerprint": fingerprint},
        )
        for relative, size, modified in plan["files"]:
            try:
                path = checked_path(settings.work / relative, settings.work)
                stat = path.stat()
                if stat.st_size != size or stat.st_mtime_ns != modified:
                    errors.append(relative)
                    continue
                path.unlink()
                deleted += size
            except (OSError, ValueError):
                errors.append(relative)
        run = store.unpack(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
        result = {
            **run["result"],
            "media_cleaned": {"at": time.time(), "deleted_bytes": deleted, "remaining_files": errors},
        }
        db.execute("UPDATE runs SET result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), run_id))
        return {"deleted_bytes": deleted, "remaining_files": errors}


def posting_plan(store):
    with store.connect() as db:
        clips = {c["id"]: c for r in recording_rows(store, db) for c in r["clips"]}
        result = []
        for row in db.execute("SELECT * FROM posting_plan ORDER BY scheduled_at"):
            item = dict(row)
            clip = clips[item["clip_id"]]
            item["deliveries"] = json.loads(item["deliveries"])
            item["buffer_managed"] = bool(db.execute(
                "SELECT 1 FROM buffer_publications WHERE clip_id=?", (item["clip_id"],)
            ).fetchone())
            item["title"] = clip["body"]["title"]
            item["video_sha256"] = clip["body"].get("video_sha256")
            item["run_id"] = clip["run_id"]
            item["ready"] = (
                clip["revision"] == item["revision"]
                and clip["review_status"] == "ready_to_post"
                and bool(
                    clip["body"].get("folder") and (Path(clip["body"]["folder"]) / "short.mp4").is_file()
                )
            )
            result.append(item)
        return result


def schedule(store, run_ids, start_date, local_time, zone):
    try:
        day, clock, tz = date.fromisoformat(start_date), day_time.fromisoformat(local_time), ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Choose a valid start date, daily time and time zone.") from None
    if clock.tzinfo is not None or clock.second or clock.microsecond:
        raise ValueError("Choose a local posting time in hours and minutes.")
    if not run_ids or len(run_ids) != len(set(run_ids)):
        raise ValueError("Select recordings to add to the posting plan.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = {r["id"]: r for r in recording_rows(store, db)}
        existing = {r["clip_id"] for r in db.execute("SELECT clip_id FROM posting_plan")}
        existing.update(r["clip_id"] for r in db.execute("SELECT clip_id FROM buffer_publications"))
        occupied = {r["local_date"] for r in db.execute("SELECT local_date FROM posting_plan")}
        groups = {}
        for run_id in run_ids:
            row = rows.get(run_id)
            if not row or not row["resolved"]:
                raise ValueError(
                    "Finish reviewing every clip in the selected recordings before scheduling them."
                )
            groups.setdefault(row["source_video"], []).extend(
                c for c in row["clips"] if c["review_status"] == "ready_to_post" and c["id"] not in existing
            )
        for source, group in groups.items():
            previous = [
                c["body"]["start_us"]
                for row in rows.values()
                if row["source_video"] == source
                for c in row["clips"]
                if c["id"] in existing
            ]
            if previous and any(c["body"]["start_us"] < max(previous) for c in group):
                raise ValueError(
                    "These clips come before this recording's existing planned clips. Remove its unposted entries and schedule them together to preserve source order."
                )
        selected = [
            c
            for group in groups.values()
            for c in sorted(group, key=lambda c: (c["body"]["start_us"], c["id"]))
        ]
        if not selected:
            raise ValueError("These recordings have no unscheduled final clips.")
        # Append whole recording groups; never fill holes ahead of existing planned clips.
        if occupied:
            day = max(day, date.fromisoformat(max(occupied)) + timedelta(days=1))
        for clip in selected:
            if not clip["body"].get("folder") or not (Path(clip["body"]["folder"]) / "short.mp4").is_file():
                raise ValueError("A selected final video is missing. Restore it before scheduling.")
            local = datetime.combine(day, clock, tzinfo=tz)
            utc = local.astimezone(timezone.utc)
            if (
                utc.astimezone(tz).replace(tzinfo=None) != local.replace(tzinfo=None)
                or local.utcoffset() != local.replace(fold=1).utcoffset()
            ):
                raise ValueError(
                    "The daily time crosses an ambiguous or missing daylight-saving hour. Choose another time."
                )
            if utc <= datetime.now(timezone.utc):
                raise ValueError("Choose a posting date and time in the future.")
            db.execute(
                "INSERT INTO posting_plan VALUES(?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    clip["id"],
                    clip["revision"],
                    utc.isoformat(),
                    day.isoformat(),
                    zone,
                    json.dumps({platform: {"status": "pending"} for platform in PLATFORMS}),
                ),
            )
            day += timedelta(days=1)
        return {"added": len(selected)}


def record_post(store, post_id, platform, url):
    if platform not in PLATFORMS:
        raise ValueError("Choose YouTube, Instagram or TikTok.")
    parsed = urlsplit(url.strip())
    hosts = {
        "youtube": {"youtube.com", "www.youtube.com", "youtu.be"},
        "instagram": {"instagram.com", "www.instagram.com"},
        "tiktok": {"tiktok.com", "www.tiktok.com"},
    }
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts[platform]
        or parsed.username
        or parsed.password
        or len(url) > 2000
    ):
        raise ValueError("Paste the HTTPS link to the published post on the selected platform.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM posting_plan WHERE id=?", (post_id,)).fetchone()
        if row is None:
            raise KeyError(post_id)
        deliveries = json.loads(row["deliveries"])
        if db.execute("SELECT 1 FROM buffer_publications WHERE clip_id=?", (row["clip_id"],)).fetchone():
            raise ValueError("This clip is managed through Buffer. Refresh its delivery status in Publishing.")
        deliveries[platform] = {
            "status": "posted",
            "url": url.strip(),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "method": "manual",
        }
        db.execute("UPDATE posting_plan SET deliveries=? WHERE id=?", (json.dumps(deliveries), post_id))


def unschedule(store, post_id):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM posting_plan WHERE id=?", (post_id,)).fetchone()
        if row is None:
            raise KeyError(post_id)
        if db.execute("SELECT 1 FROM buffer_publications WHERE clip_id=?", (row["clip_id"],)).fetchone():
            raise ValueError("Keep this entry while Buffer holds publishing records. Manage the posts in Publishing.")
        if any(p["status"] == "posted" for p in json.loads(row["deliveries"]).values()):
            raise ValueError("Keep this entry as a record of the posts already published.")
        db.execute("DELETE FROM posting_plan WHERE id=?", (post_id,))
