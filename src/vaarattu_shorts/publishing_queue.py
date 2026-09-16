"""Fill the available Buffer slots from final clips, retaining source order."""

from datetime import datetime, time, timedelta, timezone
import json
import uuid
from zoneinfo import ZoneInfo

from . import buffer, delivery, r2
from .contracts import video_id
from .storage import atomic_json

ZONE = "Europe/Helsinki"
CLOCK = time(9, 0)
ACCEPTED = {"posted", "scheduled", "sending"}


def source_key(value):
    try:
        return video_id(value)
    except ValueError:
        return value


def choose(store, jobs, remote_posts, limit):
    """Choose whole source groups, with the unfinished queue-tail group first."""
    with store.connect() as db:
        rows = delivery.recording_rows(store, db)
        dates = {r["id"]: r["published"] for r in db.execute("SELECT id,published FROM youtube_videos")}
        created = {r["id"]: r["created"] for r in db.execute("SELECT id,created FROM runs")}
    plan = {p["clip_id"]: p for p in delivery.posting_plan(store)}
    clips = {c["id"]: c for row in rows for c in row["clips"]}
    sources = {row["id"]: source_key(row["source_video"]) for row in rows}
    used_ids, used_hashes, watermarks = set(jobs), set(), {}
    history = []
    for job in jobs.values():
        item = job["item"]
        used_hashes.add(item["video_sha256"])
        source = sources[item["source"]]
        watermarks[source] = max(watermarks.get(source, -1), item["start_us"])
        history.append((item["due_at"] or job["created_at"], source))
    for post in plan.values():
        if any(r["status"] != "pending" for r in post["deliveries"].values()):
            clip = clips[post["clip_id"]]
            source = sources[clip["run_id"]]
            used_ids.add(clip["id"])
            used_hashes.add(clip["body"].get("video_sha256"))
            watermarks[source] = max(watermarks.get(source, -1), clip["body"]["start_us"])
            history.append((post["scheduled_at"], source))
    remote_urls = {asset.get("source") for post in remote_posts for asset in post.get("assets", [])}
    # Include matching posts made directly in Buffer, where there is no local receipt.
    for clip in clips.values():
        sha = clip["body"].get("video_sha256")
        if sha and f"{r2.PUBLIC_URL}/clips/{sha}.mp4" in remote_urls:
            used_ids.add(clip["id"])
            used_hashes.add(sha)
            source = sources[clip["run_id"]]
            watermarks[source] = max(watermarks.get(source, -1), clip["body"]["start_us"])
    groups, group_dates = {}, {}
    skipped_order = 0
    for row in rows:
        source = sources[row["id"]]
        published = dates.get(source)
        date = datetime.fromisoformat(published).timestamp() if published else created[row["id"]]
        group_dates[source] = max(group_dates.get(source, 0), date)
        if row["state"] != "completed":
            continue
        for clip in row["clips"]:
            if clip["review_status"] != "ready_to_post" or clip["id"] in used_ids:
                continue
            if clip["body"].get("video_sha256") in used_hashes:
                continue
            if clip["body"]["start_us"] <= watermarks.get(source, -1):
                skipped_order += 1
                continue
            groups.setdefault(source, []).append(clip)
    tail = max(history, key=lambda pair: datetime.fromisoformat(pair[0]).timestamp())[1] if history else None
    order = sorted(groups, key=lambda s: (s != tail, -group_dates[s], s))
    selected, moments = [], set()
    for source in order:
        for clip in sorted(groups[source], key=lambda c: (c["body"]["start_us"], c["id"])):
            moment = (source, clip["body"]["start_us"])
            sha = clip["body"].get("video_sha256")
            if moment in moments or sha in used_hashes:
                continue
            selected.append(clip)
            moments.add(moment)
            used_hashes.add(sha)
            if len(selected) == limit:
                return selected, skipped_order
    return selected, skipped_order


def fill(settings, store):
    # The caller holds buffer-publishing.lock throughout selection and submission.
    buffer.refresh(settings, store)
    api = buffer.client(settings)
    cfg, _ = buffer.selected_channels(settings, api)
    jobs = buffer.publications(store)
    unfinished = [j for j in jobs.values() if any(r["status"] not in ACCEPTED for r in j["receipts"].values())]
    if unfinished:
        raise ValueError("Finish or resolve the existing incomplete publishing request before filling the queue.")
    posts = api.posts(cfg["organization_id"], {"channelIds": list(cfg["mapping"].values())})
    remaining = buffer.capacity(api, cfg, jobs, posts)
    count = min(remaining.values())
    if not count:
        return {"scheduled": 0, "message": "The selected channels have no shared free queue slots.", "clips": []}
    selected, skipped_order = choose(store, jobs, posts, count)
    if not selected:
        return {"scheduled": 0, "message": "No unposted Ready for posting clips are available in source order.",
                "clips": [], "skipped_order": skipped_order}
    local_now = buffer.now().astimezone(ZoneInfo(ZONE))
    day = local_now.date()
    if datetime.combine(day, CLOCK, ZoneInfo(ZONE)) <= local_now + timedelta(minutes=10):
        day += timedelta(days=1)
    # Append after queued media, including posts created outside this app.
    occupied = set()
    queued_days = []
    for post in posts:
        posted_time = post.get("dueAt") or post.get("sentAt")
        if posted_time:
            date = datetime.fromisoformat(posted_time).astimezone(ZoneInfo(ZONE)).date()
            occupied.add(date)
            if post["status"] in {"scheduled", "sending", "needs_approval"}:
                queued_days.append(date)
    for job in jobs.values():
        posted_time = job["item"]["due_at"] or job["created_at"]
        if posted_time:
            date = datetime.fromisoformat(posted_time).astimezone(ZoneInfo(ZONE)).date()
            occupied.add(date)
            if any(r["status"] in {"scheduled", "sending"} for r in job["receipts"].values()):
                queued_days.append(date)
    if queued_days:
        day = max(day, max(queued_days) + timedelta(days=1))
    selected_ids = {c["id"] for c in selected}
    # Only untouched local plan entries for the selected clips may move to 09:00.
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing_plan = delivery.posting_plan(store)
        for entry in existing_plan:
            if entry["clip_id"] in selected_ids:
                if any(r["status"] != "pending" for r in entry["deliveries"].values()):
                    raise ValueError("A clip gained delivery records. Refresh before filling the queue.")
                db.execute("DELETE FROM posting_plan WHERE id=?", (entry["id"],))
            else:
                occupied.add(datetime.fromisoformat(entry["scheduled_at"]).astimezone(ZoneInfo(ZONE)).date())
                occupied.add(datetime.fromisoformat(entry["local_date"]).date())
        for clip in selected:
            # Roll back the entire plan if a final stamp or render changed during selection.
            buffer.validate_clip(settings, store, {"clip_id": clip["id"], "revision": clip["revision"],
                                                  "video_sha256": clip["body"].get("video_sha256")})
            while day in occupied:
                day += timedelta(days=1)
            due = datetime.combine(day, CLOCK, ZoneInfo(ZONE)).astimezone(timezone.utc).isoformat()
            db.execute("INSERT INTO posting_plan VALUES(?,?,?,?,?,?,?)", (
                uuid.uuid4().hex, clip["id"], clip["revision"], due, day.isoformat(), ZONE,
                json.dumps({p: {"status": "pending"} for p in delivery.PLATFORMS}),
            ))
            occupied.add(day)
            day += timedelta(days=1)
    items = [{"clip_id": c["id"], "revision": c["revision"],
              "title": c["body"]["title"].replace("<", "").replace(">", "")[:100],
              "caption": c["body"]["title"][:2200]} for c in selected]
    ticket = buffer.preview(settings, store, items, "plan")
    result = {"scheduled": 0, "clips": [], "skipped_order": skipped_order, "message": ""}
    for item in ticket["items"]:
        try:
            job = buffer.send(settings, store, ticket["id"], item["clip_id"])
        except ValueError as exc:
            result["message"] = str(exc)
            break
        if any(r["status"] not in ACCEPTED for r in job["receipts"].values()):
            result["message"] = "A submission needs attention. Check its platform results before filling again."
            break
        result["scheduled"] += 1
        result["clips"].append({"clip_id": item["clip_id"], "title": item["title"], "due_at": item["due_at"]})
    try:
        cfg["remaining"] = buffer.capacity(api, cfg, buffer.publications(store))
    except buffer.RemoteError as exc:
        result["message"] = (result["message"] + " Queue availability could not be refreshed: " + str(exc)).strip()
    else:
        cfg["refreshed_at"] = buffer.now().isoformat()
        atomic_json(settings.work / "buffer.json", cfg)
    return result
