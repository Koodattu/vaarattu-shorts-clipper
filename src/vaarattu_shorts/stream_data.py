from __future__ import annotations

import re
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
    return " ".join(re.sub(r"https?://\S+", "", title.casefold()).replace("->", " ").split())


def rank_matches(metadata, streams):
    date = recording_date(metadata["title"])
    result = []
    for stream in streams:
        start = datetime.fromisoformat(stream["startTime"].replace("Z", "+00:00"))
        if date is None or start.astimezone(ZoneInfo("Europe/Helsinki")).date() != date:
            continue
        similarity = max(
            (
                SequenceMatcher(None, normalize(metadata["title"]), normalize(s["title"])).ratio()
                for s in stream.get("segments", [])
            ),
            default=0,
        )
        result.append(
            {
                "id": stream["id"],
                "startTime": stream["startTime"],
                "similarity": similarity,
                "titles": [s["title"] for s in stream.get("segments", [])],
                "identity": "likely",
                "alignment": "unknown",
            }
        )
    return sorted(result, key=lambda row: row["similarity"], reverse=True)[:10]


def enrich(metadata, config, check=lambda: None):
    try:
        with httpx.Client(timeout=15, trust_env=False) as client:
            streams = []
            for page in range(1, 21):
                check()
                data = (
                    client.get(f"{BASE}/streams", params={"page": page, "limit": 100})
                    .raise_for_status()
                    .json()
                )
                body = data.get("data", data)
                batch = body if isinstance(body, list) else body.get("streams", [])
                streams.extend(batch)
                if len(batch) < 100:
                    break
            matches = rank_matches(metadata, streams)
            result = {"status": "timing_unconfirmed", "matches": matches, "points": [], "coverage": "unknown"}
            if not config.get("alignment_confirmed"):
                return result
            stream_id = config["stream_id"]
            selected = next((s for s in streams if s["id"] == stream_id), None)
            if selected is None:
                return {**result, "status": "stream_unavailable"}
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
