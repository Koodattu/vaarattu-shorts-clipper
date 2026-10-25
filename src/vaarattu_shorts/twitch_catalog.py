"""Import public archive metadata with Twitch's app-token API."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx


def configured():
    return all(os.environ.get(key, "").strip() for key in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"))


def request(client, method, url, **kwargs):
    try:
        response = client.request(method, url, **kwargs)
        if response.status_code in {400, 401, 403}:
            raise ValueError("Check TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env, then restart.")
        response.raise_for_status()
    except httpx.HTTPError:
        raise ValueError("Twitch could not complete the request. Your saved videos were preserved.") from None
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError
        return result
    except ValueError:
        raise ValueError(
            "Twitch returned an unreadable response. Your saved videos were preserved."
        ) from None


def thumbnail_url(value):
    value = (value or "").replace("%{width}", "320").replace("%{height}", "180")
    parsed = urlparse(value)
    if parsed.scheme == "https" and (parsed.hostname or "").endswith((".ttvnw.net", ".jtvnw.net")):
        return value
    return ""


def remember_source(settings, store, metadata):
    """Retain a directly entered VOD before it expires, even without API credentials."""
    if (
        metadata.get("provider") != "twitch"
        or (metadata.get("owner") or "").lower() != settings.twitch_channel
        or not isinstance(metadata.get("timestamp"), (int, float))
    ):
        return
    try:
        published = datetime.fromtimestamp(metadata["timestamp"], timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return
    store.remember_twitch_video(
        settings.twitch_channel,
        {
            "id": metadata["id"],
            "title": metadata["title"],
            "duration": metadata["duration"],
            "url": metadata["url"],
            "provider": "twitch",
            "available": True,
            "is_live": False,
            "published": published,
        },
    )


def fetch(settings, store):
    if not configured():
        raise ValueError(
            "Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env, then restart to fetch Twitch VODs."
        )
    # Tokens exist only for this refresh. Never persist credentials or API response bodies.
    with httpx.Client(timeout=15, trust_env=False) as client:
        token = request(
            client,
            "POST",
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": os.environ["TWITCH_CLIENT_ID"].strip(),
                "client_secret": os.environ["TWITCH_CLIENT_SECRET"].strip(),
                "grant_type": "client_credentials",
            },
        ).get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Twitch did not return an app access token. Check the Twitch app credentials.")
        client.headers.update(
            {"Client-ID": os.environ["TWITCH_CLIENT_ID"].strip(), "Authorization": f"Bearer {token}"}
        )
        try:
            users = request(
                client, "GET", "https://api.twitch.tv/helix/users", params={"login": settings.twitch_channel}
            )["data"]
            if len(users) != 1 or users[0]["login"].lower() != settings.twitch_channel:
                raise ValueError
            owner = users[0]
            # Exclude the active broadcast, including when it already has a VOD entry.
            live = request(
                client, "GET", "https://api.twitch.tv/helix/streams", params={"user_id": owner["id"]}
            )["data"]
            live_ids = {s["id"] for s in live}
            videos, cursors, cursor = {}, set(), None
            for _ in range(20):
                params = {"user_id": owner["id"], "type": "archive", "sort": "time", "first": 100}
                if cursor:
                    params["after"] = cursor
                page = request(client, "GET", "https://api.twitch.tv/helix/videos", params=params)
                for item in page["data"]:
                    identity = item["id"]
                    if (
                        not re.fullmatch(r"\d+", identity)
                        or item["user_id"] != owner["id"]
                        or item["type"] != "archive"
                        or not isinstance(item["title"], str)
                    ):
                        raise ValueError
                    duration = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", item["duration"])
                    if not duration or not any(duration.groups()):
                        raise ValueError
                    seconds = sum(int(n or 0) * m for n, m in zip(duration.groups(), (3600, 60, 1)))
                    created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                    if created.tzinfo is None:
                        raise ValueError
                    preview = item.get("thumbnail_url") or ""
                    videos[identity] = {
                        "id": identity,
                        "provider": "twitch",
                        "title": item["title"],
                        "published": item["created_at"],
                        "duration": seconds,
                        "url": f"https://www.twitch.tv/videos/{identity}",
                        "thumbnail": thumbnail_url(preview),
                        "is_live": item.get("stream_id") in live_ids or "404_processing" in preview,
                        "available": bool(
                            seconds
                            and item.get("stream_id") not in live_ids
                            and item.get("viewable") == "public"
                            and preview
                            and "404_processing" not in preview
                        ),
                    }
                cursor = page["pagination"].get("cursor")
                if not cursor:
                    break
                if not isinstance(cursor, str) or len(cursor) > 4096 or cursor in cursors:
                    raise ValueError
                cursors.add(cursor)
            else:
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError(
                "Twitch returned an incomplete archive list. Your saved videos were preserved."
            ) from None
    # Expire missing entries only after every page succeeded; keep their identity/history.
    store.save_twitch_catalog(
        {"id": settings.twitch_channel, "title": owner["display_name"], "fetched_at": time.time()},
        list(videos.values()),
    )
    return len(videos)
