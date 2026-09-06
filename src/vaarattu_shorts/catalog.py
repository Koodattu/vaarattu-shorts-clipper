from __future__ import annotations

import os
import re
import time

import httpx

from .contracts import video_id


def duration_seconds(value):
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value)
    if not match:
        return None
    return sum(int(n or 0) * multiplier for n, multiplier in zip(match.groups(), (86400, 3600, 60, 1)))


def request(client, resource, **params):
    try:
        response = client.get(resource, params=params)
    except httpx.TransportError:
        raise ValueError("YouTube could not be reached. Your saved videos are still available.") from None
    if response.status_code != 200:
        if response.status_code in {400, 401, 403}:
            message = "Check the YouTube API key, enabled YouTube Data API v3, restrictions and quota."
        elif response.status_code == 404:
            message = "YouTube could not find this channel or uploads playlist."
        else:
            message = "YouTube could not complete the request. Try again later."
        raise ValueError(message)
    try:
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise ValueError
        return result
    except ValueError:
        raise ValueError(
            "YouTube returned an unreadable video list. Your saved videos were preserved."
        ) from None


def fetch_page(settings, store, older=False):
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise ValueError("Set YOUTUBE_API_KEY in .env, then restart the app to fetch channel videos.")
    saved = store.channel(settings.youtube_channel_id)
    if older and (not saved or not saved.get("next_page_token")):
        raise ValueError("No older page is available. Fetch the latest videos first.")
    # Keep the key out of URLs, stored metadata and error messages.
    with httpx.Client(
        base_url="https://www.googleapis.com/youtube/v3/",
        headers={"x-goog-api-key": key},
        timeout=15,
        trust_env=False,
    ) as client:
        channel = saved
        if not older:
            result = request(
                client, "channels", part="snippet,contentDetails", id=settings.youtube_channel_id
            )
            if not result["items"]:
                raise ValueError("No channel was found. Check YOUTUBE_CHANNEL_ID in .env.")
            try:
                item = result["items"][0]
                if item["id"] != settings.youtube_channel_id:
                    raise ValueError
                channel = {
                    "id": item["id"],
                    "title": item["snippet"]["title"],
                    "uploads": item["contentDetails"]["relatedPlaylists"]["uploads"],
                }
            except (KeyError, TypeError, ValueError):
                raise ValueError("YouTube did not return this channel's uploads playlist.") from None
        params = {"part": "contentDetails", "playlistId": channel["uploads"], "maxResults": 50}
        if older:
            params["pageToken"] = saved["next_page_token"]
        page = request(client, "playlistItems", **params)
        try:
            ids = list(dict.fromkeys(video_id(item["contentDetails"]["videoId"]) for item in page["items"]))
            token = page.get("nextPageToken")
            if token is not None and (not isinstance(token, str) or len(token) > 4096):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                "YouTube returned an invalid uploads page. Your saved videos were preserved."
            ) from None
        details = (
            request(client, "videos", part="snippet,contentDetails,status", id=",".join(ids))
            if ids
            else {"items": []}
        )
        videos = []
        try:
            for item in details["items"]:
                snippet = item["snippet"]
                if item["id"] not in ids or snippet["channelId"] != settings.youtube_channel_id:
                    raise ValueError
                duration = duration_seconds(item["contentDetails"].get("duration", ""))
                available = (
                    snippet.get("liveBroadcastContent", "none") == "none"
                    and item["status"].get("uploadStatus") == "processed"
                    and item["status"].get("privacyStatus") in {"public", "unlisted"}
                    and bool(duration)
                )
                videos.append(
                    {
                        "id": item["id"],
                        "title": snippet["title"],
                        "published": snippet["publishedAt"],
                        "duration": duration,
                        "available": available,
                        "url": f"https://www.youtube.com/watch?v={item['id']}",
                    }
                )
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                "YouTube returned incomplete video details. Your saved videos were preserved."
            ) from None
        # Deleted/private entries may be omitted by videos.list. Do not leave old entries selectable.
        found = {v["id"] for v in videos}
        previous = {v["id"]: v for v in store.videos(settings.youtube_channel_id)}
        for missing in set(ids) - found:
            old = previous.get(missing, {})
            videos.append(
                {
                    "id": missing,
                    "title": old.get("title", "Unavailable video"),
                    "published": old.get("published", ""),
                    "duration": old.get("duration"),
                    "available": False,
                    "url": f"https://www.youtube.com/watch?v={missing}",
                }
            )
        store.save_video_page({**channel, "next_page_token": token, "fetched_at": time.time()}, videos)
    return len(videos)
