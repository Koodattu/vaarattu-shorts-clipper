from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

import httpx
import numpy as np

BASE = "https://dev.vaarattu.tv/api"


def recording_date(title):
    match = re.match(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\b", title)
    if not match:
        return None
    try:
        return datetime(int(match[3]), int(match[2]), int(match[1])).date()
    except ValueError:
        return None


def normalize(title):
    title = re.sub(r"https?://\S+", "", title.casefold())
    if recording_date(title):
        title = re.sub(r"^\s*\d{1,2}\.\d{1,2}\.\d{4}\b", "", title)
    return " ".join(re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", title)).split())


def rank_matches(metadata, streams):
    date = recording_date(metadata["title"])
    result = []
    for stream in streams:
        start = datetime.fromisoformat(stream["startTime"].replace("Z", "+00:00"))
        if date and start.astimezone(ZoneInfo("Europe/Helsinki")).date() != date:
            continue
        similarity = max(
            (
                SequenceMatcher(None, normalize(metadata["title"]), normalize(s["title"])).ratio()
                for s in stream.get("segments", [])
            ),
            default=0,
        )
        if similarity < 0.45:
            continue
        result.append(
            {
                "id": stream["id"],
                "startTime": stream["startTime"],
                "similarity": similarity,
                "titles": [s["title"] for s in stream.get("segments", [])],
                "identity": "likely",
                "alignment": "unknown",
                "reason": "Recording date and similar title" if date else "Similar title",
                "streamOffsetSeconds": None,
            }
        )
    return sorted(result, key=lambda row: row["similarity"], reverse=True)[:10]


def search(metadata, check=lambda: None):
    """Prefer the backend's saved associations; support older API deployments."""
    try:
        with httpx.Client(timeout=15, trust_env=False) as client:
            check()
            params = {"q": metadata.get("title", "")[:300], "limit": 10}
            if metadata.get("id"):
                params["youtubeId"] = metadata["id"]
            response = client.get(f"{BASE}/streams/search", params=params)
            if response.status_code not in {400, 404}:
                data = response.raise_for_status().json()["data"]
                if not isinstance(data["matches"], list):
                    raise ValueError("Invalid stream matches")
                return {**data, "status": "ready", "source": "search"}
            streams = []
            complete = False
            for page in range(1, 21):
                check()
                data = client.get(f"{BASE}/streams", params={"page": page, "limit": 100}).raise_for_status().json()
                batch = data["data"]
                streams.extend(batch)
                if len(batch) < 100:
                    complete = True
                    break
            matches = rank_matches(metadata, streams)
            title = normalize(metadata.get("title", ""))
            clear = (
                complete and matches and matches[0]["similarity"] >= 0.88
                and (len(matches) == 1 or matches[0]["similarity"] - matches[1]["similarity"] >= 0.1)
                and (recording_date(metadata.get("title", "")) or (len(title) >= 10 and len(title.split()) >= 2))
                and not re.search(r"\b(?:part|osa|pt)\.?\s*\d+", title)
            )
            return {
                "status": "ready", "source": "legacy", "matches": matches,
                "suggestedStreamId": matches[0]["id"] if clear else None,
                "warning": "Using title suggestions; the new search API is not deployed yet."
                + (" Only the first 2,000 streams were searched." if not complete else ""),
            }
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"status": "unavailable", "matches": [], "suggestedStreamId": None}


def stream_detail(stream_id):
    try:
        with httpx.Client(timeout=15, trust_env=False) as client:
            response = client.get(f"{BASE}/streams/{stream_id}").raise_for_status().json()
            return response["data"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise ValueError("This stream could not be loaded from vaarattu.tv. Check the ID and try again.") from None


def enrich(metadata, config, check=lambda: None):
    try:
        stream_id = config.get("stream_id")
        if not config.get("alignment_confirmed"):
            result = search(metadata, check)
            return {**result, "status": "unavailable" if result["status"] == "unavailable" else "timing_unconfirmed", "points": [], "coverage": "unknown"}
        check()
        selected = stream_detail(stream_id)
        with httpx.Client(timeout=15, trust_env=False) as client:
            result = {"matches": [], "points": [], "coverage": "unknown"}
            check()
            payload = client.get(f"{BASE}/streams/{stream_id}/activity").raise_for_status().json()
            activity = payload.get("data", payload)
            start = datetime.fromisoformat(selected["startTime"].replace("Z", "+00:00"))
            points = []
            for point in activity["points"]:
                time = datetime.fromisoformat(point["time"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(point["endTime"].replace("Z", "+00:00"))
                if end <= time or type(point["activeChatters"]) is not int or point["activeChatters"] < 0:
                    raise ValueError("Invalid activity bucket.")
                points.append(
                    {
                        "start_us": round(
                            ((time - start).total_seconds() - config["stream_offset_seconds"]) * 1e6
                        ),
                        "end_us": round(
                            ((end - start).total_seconds() - config["stream_offset_seconds"]) * 1e6
                        ),
                        "active_chatters": point["activeChatters"],
                    }
                )
            return {
                **result,
                "status": "aligned",
                "points": points,
                "interval_minutes": activity["intervalMinutes"],
                "stream_id": stream_id,
                "stream_offset_seconds": config["stream_offset_seconds"],
            }
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"status": "unavailable", "matches": [], "points": [], "coverage": "unknown"}


def peak_regions(data, duration_us):
    """Return merged VOD regions around locally unusual chatter counts, not clip scores."""
    if data.get("status") != "aligned":
        return []
    points = sorted(data.get("points", []), key=lambda p: p["start_us"])
    regions = []
    for point in points:
        if point["end_us"] <= 0 or point["start_us"] >= duration_us:
            continue
        width = point["end_us"] - point["start_us"]
        if width <= 0 or point["active_chatters"] <= 0:
            continue
        # The API does not expose collection coverage; zero buckets cannot prove a quiet baseline.
        neighbors = [
            p["active_chatters"]
            for p in points
            if p is not point
            and p["active_chatters"] > 0
            and 0 <= p["start_us"] < duration_us
            and abs(p["start_us"] - point["start_us"]) <= max(600000000, 5 * width)
        ]
        if len(neighbors) < 4:
            continue
        baseline = float(np.median(neighbors))
        mad = float(np.median(np.abs(np.array(neighbors) - baseline)))
        if point["active_chatters"] - baseline < max(3, 3 * mad, baseline * 0.5):
            continue
        evidence = {**point, "baseline": baseline, "mad": mad}
        start, end = max(0, point["start_us"] - 90000000), min(duration_us, point["end_us"] + 30000000)
        if regions and start <= regions[-1]["end_us"]:
            regions[-1]["end_us"] = max(regions[-1]["end_us"], end)
            regions[-1]["peaks"].append(evidence)
        else:
            regions.append({"start_us": start, "end_us": end, "peaks": [evidence]})
    return regions


def boost(start, end, data):
    if data["status"] != "aligned":
        return 0.0
    points = data["points"]
    for point in points:
        if point["end_us"] < start or point["start_us"] > end + 90000000:
            continue
        neighborhood = [
            p["active_chatters"] for p in points if abs(p["start_us"] - point["start_us"]) <= 300000000
        ]
        if len(neighborhood) < 3:
            continue
        median = float(np.median(neighborhood))
        mad = float(np.median(np.abs(np.array(neighborhood) - median)))
        if point["active_chatters"] - median >= max(3, 3 * mad):
            return 1.0
    return 0.0
