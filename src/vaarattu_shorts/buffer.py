"""Explicit Buffer publishing with durable, per-channel receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from . import delivery, r2
from .config import load_env, save_env
from .storage import atomic_json, digest

PLATFORMS = delivery.PLATFORMS
POST_FIELDS = "id status text dueAt channelId externalLink schedulingType assets { source } error { message }"
RETRYABLE = {"pending", "failed"}
UNCERTAIN = {"submitting", "unknown"}


class PublishItem(BaseModel):
    clip_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=100)
    caption: str | None = Field(default=None, max_length=2200)
    category: str = "20"
    made_for_kids: bool = False


class PreviewRequest(BaseModel):
    items: list[PublishItem] = Field(min_length=1, max_length=10)
    mode: Literal["now", "schedule", "plan", "resume"]
    timezone: str = Field(default="Europe/Helsinki", max_length=100)
    local_time: str = Field(default="", max_length=40)


def now():
    return datetime.now(timezone.utc)


class RemoteError(ValueError):
    pass


class Client:
    def __init__(self, key):
        self.key = key

    def message(self, value):
        return str(value or "Buffer could not complete this request.").replace(self.key, "[redacted]")[:800]

    def query(self, query, variables=None, *, mutation=False):
        # Creating a post has no documented idempotency key. Never retry it blindly.
        for attempt in range(1 if mutation else 3):
            try:
                with httpx.Client(timeout=httpx.Timeout(90, connect=20), trust_env=False) as http:
                    response = http.post("https://api.buffer.com", headers={"Authorization": f"Bearer {self.key}"},
                                         json={"query": query, "variables": variables or {}})
                if response.status_code in {429, 500, 502, 503, 504} and not mutation and attempt < 2:
                    delay = response.headers.get("Retry-After", "")
                    time.sleep(min(30, max(2, int(delay))) if delay.isdigit() else (2, 5)[attempt])
                    continue
                if not response.is_success:
                    raise RemoteError(f"Buffer returned HTTP {response.status_code}. Check access or try again later.")
                body = response.json()
                if not isinstance(body, dict):
                    raise RemoteError("Buffer returned an incomplete response. Refresh its status before retrying.")
                if body.get("errors"):
                    raise RemoteError(self.message(body["errors"][0].get("message")))
                if not isinstance(body.get("data"), dict):
                    raise RemoteError("Buffer returned an incomplete response. Refresh its status before retrying.")
                return body["data"]
            except (httpx.HTTPError, json.JSONDecodeError):
                if not mutation and attempt < 2:
                    time.sleep((2, 5)[attempt])
                    continue
                raise RemoteError("Buffer could not be reached. Refresh its status before retrying.") from None

    def organizations(self):
        return self.query("query Organizations { account { organizations { id name } } }")["account"]["organizations"]

    def channels(self, organization_id):
        return self.query("query Channels($input: ChannelsInput!) { channels(input:$input) { "
                          "id name displayName service organizationId isDisconnected isLocked isQueuePaused } }",
                          {"input": {"organizationId": organization_id}})["channels"]

    def posts(self, organization_id, filters):
        result, cursor = [], None
        for _ in range(30):
            data = self.query("query Posts($input: PostsInput!, $after: String) { "
                              "posts(input:$input, first:100, after:$after) { edges { node { " + POST_FIELDS +
                              " } } pageInfo { hasNextPage endCursor } } }",
                              {"input": {"organizationId": organization_id, "filter": filters}, "after": cursor})["posts"]
            result.extend(e["node"] for e in (data["edges"] or []))
            if data["pageInfo"]["hasNextPage"] is False:
                return result
            next_cursor = data["pageInfo"].get("endCursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        raise RemoteError("Buffer's post listing is incomplete. Nothing new was submitted.")

    def post(self, post_id):
        return self.query("query Post($input: PostInput!) { post(input:$input) { " + POST_FIELDS + " } }",
                          {"input": {"id": post_id}})["post"]

    def create(self, body):
        return self.query("mutation CreatePost($input: CreatePostInput!) { createPost(input:$input) { __typename "
                          "... on PostActionSuccess { post { " + POST_FIELDS + " } } "
                          "... on MutationError { message } } }", {"input": body}, mutation=True)["createPost"]


def client(settings):
    load_env(settings.root / ".env")
    key = os.environ.get("BUFFER_API_KEY", "")
    if not key:
        raise ValueError("Connect your Buffer account in Publishing first.")
    return Client(key)


def config(settings):
    path = settings.work / "buffer.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {"organizations": [], "channels": [], "mapping": {}}


def connect(settings, key):
    if not isinstance(key, str) or not 16 <= len(key.strip()) <= 4096 or re.search(r"[\s']", key.strip()):
        raise ValueError("Paste your personal Buffer API key.")
    key = key.strip()
    api = Client(key)
    organizations = api.organizations()
    if not organizations:
        raise ValueError("This Buffer account has no organization. Complete its setup in Buffer first.")
    old = config(settings)
    org = next((o["id"] for o in organizations if o["id"] == old.get("organization_id")), organizations[0]["id"])
    channels = api.channels(org)
    mapping = {}
    for platform in PLATFORMS:
        choices = [c["id"] for c in channels if c["service"] == platform]
        previous = old.get("mapping", {}).get(platform)
        if previous in choices:
            mapping[platform] = previous
        elif len(choices) == 1:
            mapping[platform] = choices[0]
    save_env(settings.root / ".env", {"BUFFER_API_KEY": key})
    os.environ["BUFFER_API_KEY"] = key
    atomic_json(settings.work / "buffer.json", {"organizations": organizations, "organization_id": org,
                                               "channels": channels, "mapping": mapping})
    return config(settings)


def configure(settings, organization_id, mapping):
    api = client(settings)
    organizations = api.organizations()
    if organization_id not in {o["id"] for o in organizations}:
        raise ValueError("Choose an organization from your Buffer account.")
    channels = api.channels(organization_id)
    if not isinstance(mapping, dict) or set(mapping) - set(PLATFORMS):
        raise ValueError("Choose channels for YouTube, Instagram and TikTok.")
    for platform, channel in mapping.items():
        if channel and channel not in {c["id"] for c in channels if c["service"] == platform}:
            raise ValueError("A selected channel does not match its platform. Refresh your Buffer channels.")
    result = {"organizations": organizations, "organization_id": organization_id,
              "channels": channels, "mapping": {p: c for p, c in mapping.items() if c}}
    atomic_json(settings.work / "buffer.json", result)
    return result


def publications(store):
    with store.connect() as db:
        return {row["clip_id"]: json.loads(row["body"]) for row in db.execute("SELECT * FROM buffer_publications")}


def save_publication(store, job):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM posting_plan WHERE clip_id=?", (job["clip_id"],)).fetchone()
        existing = db.execute("SELECT 1 FROM buffer_publications WHERE clip_id=?", (job["clip_id"],)).fetchone()
        if row and not existing and any(r["status"] != "pending" for r in json.loads(row["deliveries"]).values()):
            raise ValueError("This clip gained delivery records since preview. Check them before publishing.")
        db.execute("INSERT INTO buffer_publications VALUES(?,?) ON CONFLICT(clip_id) DO UPDATE SET body=excluded.body",
                   (job["clip_id"], json.dumps(job)))
        if row:
            receipts = json.loads(row["deliveries"])
            for platform, receipt in job["receipts"].items():
                receipts[platform] = {**receipt, "method": "buffer"}
            db.execute("UPDATE posting_plan SET deliveries=? WHERE clip_id=?", (json.dumps(receipts), job["clip_id"]))


def status(settings, store):
    load_env(settings.root / ".env")
    jobs = publications(store)
    plan = {p["clip_id"]: p for p in delivery.posting_plan(store)}
    clips = []
    with store.connect() as db:
        for run in delivery.recording_rows(store, db):
            for clip in run["clips"]:
                if clip["review_status"] != "ready_to_post" and clip["id"] not in jobs:
                    continue
                body = clip["body"]
                clips.append({"id": clip["id"], "revision": clip["revision"], "title": body["title"],
                              "run_id": clip["run_id"], "source_title": run["title"], "start_us": body["start_us"],
                              "ready": clip["review_status"] == "ready_to_post", "video_sha256": body.get("video_sha256"),
                              "plan": plan.get(clip["id"]), "publication": jobs.get(clip["id"])})
    clips.sort(key=lambda c: (c["plan"]["scheduled_at"] if c["plan"] else "9999", c["run_id"], c["start_us"]))
    return {"connected": bool(os.environ.get("BUFFER_API_KEY")), **config(settings), "clips": clips}


def selected_channels(settings, api):
    cfg = config(settings)
    mapping = cfg.get("mapping", {})
    if not mapping or set(mapping) - set(PLATFORMS):
        raise ValueError("Select at least one Buffer channel first.")
    channels = {c["id"]: c for c in api.channels(cfg["organization_id"])}
    for platform, channel_id in mapping.items():
        channel = channels.get(channel_id)
        if not channel or channel["service"] != platform or channel["isDisconnected"] or channel["isLocked"]:
            raise ValueError(f"Reconnect or unlock the {platform} channel in Buffer before publishing.")
        if channel["isQueuePaused"]:
            raise ValueError(f"Resume the {platform} queue in Buffer before publishing.")
    return cfg, {p: channels[c] for p, c in mapping.items()}


def capacity(api, cfg, jobs):
    channel_ids = list(cfg["mapping"].values())
    posts = api.posts(cfg["organization_id"], {"channelIds": channel_ids, "status": ["scheduled", "sending", "needs_approval"]})
    ids = {p["id"] for p in posts}
    counts = {c: sum(p["channelId"] == c for p in posts) for c in channel_ids}
    for job in jobs.values():
        for receipt in job["receipts"].values():
            channel = receipt["channel_id"]
            if channel in counts and receipt["status"] in (UNCERTAIN | {"scheduled", "sending", "needs_approval", "attention"}):
                if receipt.get("post_id") not in ids:
                    counts[channel] += 1
    return {p: max(0, 10 - counts[c]) for p, c in cfg["mapping"].items()}


def validate_clip(settings, store, item):
    clip = store.clip(item["clip_id"])
    if clip["revision"] != item["revision"] or clip["review_status"] != "ready_to_post":
        raise ValueError("A selected clip changed. Review the current revision and preview publishing again.")
    path = delivery.checked_path(settings.ready / clip["id"] / str(clip["revision"]) / "short.mp4", settings.work)
    if not path.is_file() or digest(path) != item["video_sha256"] or clip["body"].get("video_sha256") != item["video_sha256"]:
        raise ValueError("A selected video changed on disk. Render and review it again before publishing.")
    return clip


def local_due(value, zone):
    try:
        local = datetime.fromisoformat(value)
        tz = ZoneInfo(zone)
        if local.tzinfo is not None:
            raise ValueError()
        aware = local.replace(tzinfo=tz)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) != local or aware.utcoffset() != aware.replace(fold=1).utcoffset():
            raise ValueError()
        return utc.isoformat()
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise ValueError("Choose a valid local time and time zone, outside ambiguous daylight-saving hours.") from None


def fingerprint(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def preview(settings, store, items, mode, zone="Europe/Helsinki", local_time=""):
    if mode not in {"now", "schedule", "plan", "resume"} or not 1 <= len(items) <= 10:
        raise ValueError("Choose one to ten clips and a publishing option.")
    if mode in {"schedule", "now", "resume"} and len(items) != 1:
        raise ValueError("Publish now or choose a custom time for one clip; use the daily plan for a batch.")
    api = client(settings)
    cfg, channels = selected_channels(settings, api)
    jobs = publications(store)
    remaining = capacity(api, cfg, jobs)
    plan = {p["clip_id"]: p for p in delivery.posting_plan(store)}
    result, seen = [], set()
    for raw in items:
        clip = store.clip(raw["clip_id"])
        if clip["id"] in seen:
            raise ValueError("Select each clip only once.")
        seen.add(clip["id"])
        previous = jobs.get(clip["id"])
        if mode == "resume":
            if not previous:
                raise ValueError("There is no saved publishing request to resume.")
            item = previous["item"]
            if any(r["status"] in UNCERTAIN for r in previous["receipts"].values()):
                raise ValueError("Resolve the uncertain Buffer request before retrying.")
        else:
            if previous:
                raise ValueError("This clip already has a publishing request. Use its delivery controls instead.")
            planned = plan.get(clip["id"])
            if planned and any(r["status"] != "pending" for r in planned["deliveries"].values()):
                raise ValueError("This clip already has delivery records. Keep them to avoid duplicate posts.")
            due_at = None
            if mode == "schedule":
                due_at = local_due(local_time, zone)
            elif mode == "plan":
                if not planned or planned["revision"] != raw["revision"]:
                    raise ValueError("Add each selected final revision to the daily posting plan first.")
                due_at, zone = planned["scheduled_at"], planned["timezone"]
            title = raw.get("title", clip["body"]["title"]).strip()
            caption = raw.get("caption", title).strip()
            if not title or len(title) > 100 or len(caption) > 2200 or "<" in title or ">" in title:
                raise ValueError("Use a title of 1–100 characters without angle brackets and a caption up to 2,200 characters.")
            category = raw.get("category", "20")
            if category not in {"1", "2", "10", "15", "17", "19", "20", "22", "23", "24", "25", "26", "27", "28", "29"}:
                raise ValueError("Choose a valid YouTube category.")
            item = {"clip_id": clip["id"], "revision": raw["revision"], "video_sha256": clip["body"].get("video_sha256"),
                    "title": title, "caption": caption, "mode": "now" if mode == "now" else "schedule",
                    "due_at": due_at, "timezone": zone, "organization_id": cfg["organization_id"],
                    "mapping": cfg["mapping"], "source": clip["run_id"], "start_us": clip["body"]["start_us"],
                    "category": category, "made_for_kids": raw.get("made_for_kids", False)}
            item["plan_id"] = planned["id"] if mode == "plan" else None
            if not isinstance(item["made_for_kids"], bool):
                raise ValueError("Choose the YouTube audience setting.")
        validate_clip(settings, store, item)
        if item["mapping"] != cfg["mapping"] or item["organization_id"] != cfg["organization_id"]:
            raise ValueError("Restore this request's original Buffer channels before retrying it.")
        if item["due_at"] and datetime.fromisoformat(item["due_at"]) <= now() + timedelta(minutes=2):
            raise ValueError("Choose a publishing time at least two minutes in the future.")
        needed = [p for p in cfg["mapping"] if not previous or previous["receipts"][p]["status"] in RETRYABLE]
        if not needed:
            raise ValueError("No unsubmitted platforms remain. Refresh delivery status or manage the posts in Buffer.")
        for platform in needed:
            if remaining[platform] <= 0:
                raise ValueError(f"The {platform} channel has no free queue slots. Buffer Free allows ten queued posts per channel.")
            remaining[platform] -= 1
        result.append(item)
    result.sort(key=lambda i: (i["due_at"] or "", i["source"], i["start_us"]))
    preview_id = uuid.uuid4().hex
    body = {"items": result, "channels": channels, "remaining_after": remaining}
    with store.connect() as db:
        db.execute("DELETE FROM buffer_previews WHERE expires<?", (time.time(),))
        db.execute("INSERT INTO buffer_previews VALUES(?,?,?)", (preview_id, time.time() + 3600, json.dumps(body)))
    return {"id": preview_id, **body}


def receipt_from_post(api, post, channel_id):
    if not isinstance(post, dict) or not post.get("id") or post.get("channelId") != channel_id:
        raise RemoteError("Buffer returned an unexpected post. Check it before retrying.")
    remote_status = post.get("status")
    status = "posted" if remote_status == "sent" else remote_status
    if post.get("schedulingType") != "automatic" or status not in {"posted", "scheduled", "sending", "error", "draft", "needs_approval"}:
        status = "attention"
    url = post.get("externalLink") or ""
    if url and not url.startswith("https://"):
        url = ""
    return {"status": status, "post_id": post["id"], "channel_id": channel_id, "url": url,
            "due_at": post.get("dueAt"), "checked_at": now().isoformat(),
            "message": api.message(post["error"].get("message")) if post.get("error") else
            ("Buffer requires attention; automatic publication is not confirmed." if status == "attention" else "")}


def send(settings, store, preview_id, clip_id):
    with store.connect() as db:
        row = db.execute("SELECT * FROM buffer_previews WHERE id=?", (preview_id,)).fetchone()
    if not row or row["expires"] < time.time():
        raise ValueError("This publishing preview expired. Preview the clips again.")
    ticket = json.loads(row["body"])
    item = next((i for i in ticket["items"] if i["clip_id"] == clip_id), None)
    if item is None:
        raise ValueError("This clip was not part of the approved preview.")
    jobs = publications(store)
    for earlier in ticket["items"][:ticket["items"].index(item)]:
        previous = jobs.get(earlier["clip_id"])
        if not previous or any(r["status"] not in {"posted", "scheduled", "sending"} for r in previous["receipts"].values()):
            raise ValueError("Finish the earlier clip's submissions before continuing this batch.")
    job = jobs.get(clip_id)
    if job and job["fingerprint"] != fingerprint(item):
        raise ValueError("This clip already has a different publishing request. Check its delivery records.")
    if job and any(r["status"] in UNCERTAIN for r in job["receipts"].values()):
        raise ValueError("A Buffer request has an uncertain outcome. Refresh or resolve it before retrying.")
    if job and all(r["status"] not in RETRYABLE for r in job["receipts"].values()):
        return job
    api = client(settings)
    cfg, _ = selected_channels(settings, api)
    if cfg["mapping"] != item["mapping"] or cfg["organization_id"] != item["organization_id"]:
        raise ValueError("Buffer channel selection changed. Preview publishing again.")
    validate_clip(settings, store, item)
    if item.get("plan_id"):
        with store.connect() as db:
            planned = db.execute("SELECT * FROM posting_plan WHERE id=?", (item["plan_id"],)).fetchone()
        if not planned or planned["scheduled_at"] != item["due_at"] or planned["revision"] != item["revision"]:
            raise ValueError("The daily posting plan changed. Preview publishing again.")
    remaining = capacity(api, cfg, jobs)
    for platform in item["mapping"]:
        if (not job or job["receipts"][platform]["status"] in RETRYABLE) and remaining[platform] == 0:
            raise ValueError(f"The {platform} queue filled since preview. Refresh before retrying.")
    media = r2.upload(settings, store, clip_id, item["revision"])
    validate_clip(settings, store, item)
    if not job:
        job = {"clip_id": clip_id, "revision": item["revision"], "item": item, "fingerprint": fingerprint(item),
               "media": media, "created_at": now().isoformat(),
               "receipts": {p: {"status": "pending", "channel_id": c} for p, c in item["mapping"].items()}}
        save_publication(store, job)
    for platform, receipt in job["receipts"].items():
        if receipt["status"] not in RETRYABLE:
            continue
        validate_clip(settings, store, item)
        if item["due_at"] and datetime.fromisoformat(item["due_at"]) <= now():
            raise ValueError("The scheduled time passed during upload. Manage the saved request before trying again.")
        payload = {"channelId": receipt["channel_id"], "text": item["caption"],
                   "assets": [{"video": {"url": media["url"]}}], "schedulingType": "automatic",
                   "mode": "shareNow" if item["mode"] == "now" else "customScheduled", "needsApproval": False,
                   "saveToDraft": False, "source": "vaarattu-shorts"}
        if item["due_at"]:
            payload["dueAt"] = item["due_at"]
        if platform == "youtube":
            payload["metadata"] = {"youtube": {"title": item["title"], "categoryId": item["category"],
                                                "privacy": "public", "madeForKids": item["made_for_kids"]}}
        elif platform == "instagram":
            payload["metadata"] = {"instagram": {"type": "reel", "shouldShareToFeed": True}}
        receipt.update(status="submitting", attempted_at=now().isoformat())
        save_publication(store, job)
        try:
            response = api.create(payload)
            if not isinstance(response, dict):
                raise RemoteError("Buffer returned an incomplete result. Refresh before retrying.")
            if response.get("__typename") != "PostActionSuccess":
                known_failure = response.get("__typename") in {"InvalidInputError", "LimitReachedError", "UnauthorizedError", "NotFoundError"}
                receipt.update(status="failed" if known_failure else "unknown", message=api.message(response.get("message")))
            else:
                receipt.update(receipt_from_post(api, response.get("post"), receipt["channel_id"]))
        except RemoteError as exc:
            receipt.update(status="unknown", message=str(exc))
        save_publication(store, job)
        if receipt["status"] not in {"posted", "scheduled", "sending"}:
            break
    return job


def refresh(settings, store):
    api = client(settings)
    cfg = config(settings)
    organizations = api.organizations()
    if cfg.get("organization_id") not in {o["id"] for o in organizations}:
        raise ValueError("Reconnect Buffer or choose an accessible organization.")
    cfg.update(organizations=organizations, channels=api.channels(cfg["organization_id"]))
    jobs = publications(store)
    for job in jobs.values():
        for receipt in job["receipts"].values():
            if receipt["status"] == "posted":
                continue
            try:
                post = None
                if receipt.get("post_id"):
                    post = api.post(receipt["post_id"])
                elif receipt["status"] in UNCERTAIN:
                    start = datetime.fromisoformat(receipt["attempted_at"]) - timedelta(minutes=2)
                    matches = api.posts(job["item"]["organization_id"], {"channelIds": [receipt["channel_id"]],
                                        "createdAt": {"start": start.isoformat()}})
                    matches = [p for p in matches if p["channelId"] == receipt["channel_id"] and
                               p.get("text") == job["item"]["caption"] and
                               (not job["item"]["due_at"] or (p.get("dueAt") and
                                datetime.fromisoformat(p["dueAt"]) == datetime.fromisoformat(job["item"]["due_at"]))) and
                               any(a.get("source") == job["media"]["url"] for a in p.get("assets", []))]
                    if len(matches) == 1:
                        post = matches[0]
                    else:
                        receipt.update(status="unknown", message="No unique matching post found. Check Buffer before allowing a retry.")
                if post:
                    receipt.update(receipt_from_post(api, post, receipt["channel_id"]))
            except RemoteError as exc:
                receipt["message"] = str(exc)
        save_publication(store, job)
    cfg["remaining"] = capacity(api, cfg, jobs) if cfg.get("mapping") else {}
    cfg["refreshed_at"] = now().isoformat()
    atomic_json(settings.work / "buffer.json", cfg)
    return status(settings, store)


def resolve(settings, store, clip_id, platform, post_id="", absent_confirmed=False):
    job = publications(store).get(clip_id)
    if not job or platform not in job["receipts"] or job["receipts"][platform]["status"] not in UNCERTAIN:
        raise ValueError("Only an uncertain submission can be resolved here.")
    receipt = job["receipts"][platform]
    if post_id:
        api = client(settings)
        post = api.post(post_id)
        if not any(a.get("source") == job["media"]["url"] for a in post.get("assets", [])):
            raise ValueError("This Buffer post does not contain the uploaded video.")
        receipt.update(receipt_from_post(api, post, receipt["channel_id"]))
    elif absent_confirmed is True:
        receipt.update(status="pending", message="You confirmed that Buffer did not create a post.")
    else:
        raise ValueError("Enter the matching Buffer post ID or confirm that no post was created.")
    save_publication(store, job)
    return job


def discard_unsubmitted(store, clip_id):
    job = publications(store).get(clip_id)
    if not job or any(r.get("post_id") or r["status"] not in RETRYABLE for r in job["receipts"].values()):
        raise ValueError("Keep this record: Buffer has or may have created a post. Resolve it first.")
    with store.connect() as db:
        db.execute("DELETE FROM buffer_publications WHERE clip_id=?", (clip_id,))
        row = db.execute("SELECT deliveries FROM posting_plan WHERE clip_id=?", (clip_id,)).fetchone()
        if row:
            receipts = json.loads(row["deliveries"])
            for platform in job["receipts"]:
                if receipts[platform].get("method") == "buffer":
                    receipts[platform] = {"status": "pending"}
            db.execute("UPDATE posting_plan SET deliveries=? WHERE clip_id=?", (json.dumps(receipts), clip_id))


def remove_published_media(settings, store, clip_id):
    jobs = publications(store)
    job = jobs.get(clip_id)
    if not job or job.get("media_removed_at"):
        raise ValueError("There is no hosted copy to remove.")
    related = [j for j in jobs.values() if j["media"]["url"] == job["media"]["url"]]
    if any(r["status"] != "posted" for j in related for r in j["receipts"].values()):
        raise ValueError("Wait until every tracked platform confirms publication before removing the hosted copy.")
    if any(datetime.fromisoformat(r["checked_at"]) > now() - timedelta(hours=48) for j in related for r in j["receipts"].values()):
        raise ValueError("Keep the hosted copy for 48 hours after publication was confirmed.")
    key = f"clips/{job['media']['sha256']}.mp4"
    r2.Client(**r2.credentials(settings)).request("DELETE", key)
    saved = r2.uploads(settings)
    for identity, record in list(saved.items()):
        if record["url"] == job["media"]["url"]:
            del saved[identity]
    atomic_json(settings.work / "r2-uploads.json", saved)
    for related_job in related:
        related_job["media_removed_at"] = now().isoformat()
        save_publication(store, related_job)
    return {"removed": True}
