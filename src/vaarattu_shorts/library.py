"""Conservative recording equivalence and provider-independent shorts history.

Matching identifies an entire export, never a media-clock offset. Only a complete,
unique numbered export with the same date/title and duration shares history.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from .contracts import recording_id
from .highlight_sources import PART, ordered_parts, part_info
from .stream_data import normalize, recording_date


def matches(videos):
    groups, singles, twitch = defaultdict(list), [], []
    status = {v["url"]: "unmatched" for v in videos}
    for video in videos:
        if video["provider"] == "twitch":
            twitch.append(video)
            continue
        try:
            part = part_info(video["title"])
        except ValueError:
            status[video["url"]] = "incomplete_parts"
            continue
        if part:
            groups[(part[0], part[2])].append(video)
        else:
            singles.append([video])
    exports = singles
    for group in groups.values():
        try:
            exports.append(ordered_parts(group))
        except ValueError:
            for video in group:
                status[video["url"]] = "incomplete_parts"
    proposals = []
    for group in exports:
        title = PART.sub("", group[0]["title"])
        date, name = recording_date(title), normalize(title)
        if not date or not name or any(not v.get("duration") for v in group):
            continue
        duration = sum(v["duration"] for v in group)
        for vod in twitch:
            if vod.get("is_live"):
                continue
            try:
                start = datetime.fromisoformat(vod["published"].replace("Z", "+00:00"))
                if start.tzinfo is None:
                    continue
            except (ValueError, TypeError):
                continue
            if (
                start.astimezone(ZoneInfo("Europe/Helsinki")).date() == date
                and normalize(vod["title"]) == name
                and vod.get("duration")
                and abs(duration - vod["duration"]) <= 3 * len(group)
            ):
                proposals.append((vod, group))
    uses = defaultdict(int)
    for vod, group in proposals:
        for video in [vod, *group]:
            uses[video["url"]] += 1
    accepted = []
    for vod, group in proposals:
        unique = all(uses[v["url"]] == 1 for v in [vod, *group])
        for video in [vod, *group]:
            status[video["url"]] = "matched" if unique else "ambiguous"
        if unique:
            accepted.append((vod, group))
    return accepted, status


def with_history(videos, runs):
    history = defaultdict(list)
    for run in runs:  # Newest first, including history older than the run-list UI limit.
        try:
            key = recording_id(run["config"].get("video", ""))
        except (ValueError, AttributeError):
            continue
        history[key].append(run)
    rows = {}
    for video in videos:
        direct = history[recording_id(video["url"])]
        latest = direct[0] if direct else {}
        complete = [
            r
            for r in direct
            if (r["state"] == "completed" and r["result"].get("coverage") != "partial")
            or r["result"].get("coverage") == "complete"
        ]
        rows[video["url"]] = {
            **video,
            "run_id": latest.get("id"),
            "state": latest.get("state", "not_started"),
            "outcome": latest.get("result", {}).get("outcome"),
            "processed": bool(complete),
            "completed_run_id": complete[0]["id"] if complete else None,
            "processed_via_match": False,
            "partially_processed": False,
            "matched_sources": [],
        }
    matched, status = matches(videos)
    for vod, group in matched:
        whole, parts = rows[vod["url"]], [rows[v["url"]] for v in group]
        # Use direct completion only. A finished part must never complete its siblings.
        if whole["processed"]:
            for part in parts:
                if not part["processed"]:
                    part.update(
                        processed=True, processed_via_match=True, completed_run_id=whole["completed_run_id"]
                    )
        elif all(part["processed"] for part in parts):
            whole.update(
                processed=True, processed_via_match=True, completed_run_id=parts[0]["completed_run_id"]
            )
        elif any(part["processed"] for part in parts):
            whole["partially_processed"] = True
        for row in [whole, *parts]:
            row["matched_sources"] = [
                {
                    "url": v["url"],
                    "title": v["title"],
                    "provider": v["provider"],
                    "processed": v["processed"],
                    "run_id": v["completed_run_id"],
                }
                for v in ([whole] if row is not whole else parts)
            ]
    return [{**rows[v["url"]], "match_status": status[v["url"]]} for v in videos]
