"""Conservative source-to-output edits: quiet audio AND unoccupied transcript gaps."""

from __future__ import annotations

import math
import re

from .contracts import MIN_CLIP_US
from .processes import run_tool


def quiet_intervals(log, origin_us, end_us):
    intervals, start = [], None
    for kind, value in re.findall(r"silence_(start|end):\s*([-+\d.eE]+)", log):
        seconds = float(value)
        if not math.isfinite(seconds):
            raise ValueError("Silence timing could not be read safely.")
        point = origin_us + round(seconds * 1e6)
        if kind == "start":
            start = point
        elif start is not None:
            if point > start:
                intervals.append((start, min(point, end_us)))
            start = None
    # A trailing silence has no following speech and is not an internal pacing edit.
    return intervals


def plan(start, end, words, quiet, timing_issues=()):
    # Merge occupied word spans, including nested ASR overlaps, before finding gaps.
    occupied = []
    for word in sorted(words, key=lambda w: w.start_us):
        a, b = max(start, word.start_us), min(end, word.end_us)
        if b <= a:
            continue
        if occupied and a <= occupied[-1][1]:
            occupied[-1][1] = max(occupied[-1][1], b)
        else:
            occupied.append([a, b])
    removed = []
    for left, right in zip(occupied, occupied[1:]):
        gap_start, gap_end = left[1], right[0]
        if gap_end - gap_start < 2000000 or any(
            i["start_us"] < gap_end and i["end_us"] > gap_start for i in timing_issues
        ):
            continue
        for a, b in quiet:
            a, b = max(a, gap_start), min(b, gap_end)
            if b - a < 2000000:
                continue
            # Preserve half a second at either end, including around untranscribed sound.
            removed.append([a + 500000, b - 500000])
    removed.sort()
    retained, cursor = [], start
    for a, b in removed:
        if a < cursor or b <= a:
            raise ValueError("Silence edits overlap; keep this clip's original pacing.")
        retained.append([cursor, a])
        cursor = b
    retained.append([cursor, end])
    duration = sum(b - a for a, b in retained)
    if duration < MIN_CLIP_US:
        retained, removed, duration = [[start, end]], [], end - start
    offset, spans = 0, []
    for a, b in retained:
        spans.append({"source_start_us": a, "source_end_us": b, "output_start_us": offset})
        offset += b - a
    return {"version": 1, "retained": spans, "removed": removed, "output_duration_us": duration}


def retime(words, edit_plan):
    result = []
    for word in words:
        for span in edit_plan["retained"]:
            if span["source_start_us"] <= word.start_us and word.end_us <= span["source_end_us"]:
                shift = span["output_start_us"] - span["source_start_us"]
                result.append(
                    word.model_copy(update={"start_us": word.start_us + shift, "end_us": word.end_us + shift})
                )
                break
        else:
            raise ValueError("A silence edit would remove speech. Keep the original pacing.")
    return result


def analyze(settings, section, mapping, body, words, folder, check):
    start, end = body["start_us"], body["end_us"]
    # Keep all audio channels: downmixing can cancel speech present in one channel.
    local_start = (start - mapping["origin_us"]) / 1e6
    run_tool(
        [
            settings.ffmpeg,
            "-nostdin",
            "-v",
            "info",
            "-i",
            section,
            "-vn",
            "-af",
            f"atrim=start={local_start:.6f}:duration={(end - start) / 1e6:.6f},asetpts=PTS-STARTPTS,silencedetect=n=-50dB:d=2",
            "-f",
            "null",
            "-",
        ],
        settings,
        folder,
        "silence",
        check,
    )
    quiet = quiet_intervals((folder / "silence.log").read_text("utf-8", errors="replace"), start, end)
    result = plan(start, end, words, quiet, body.get("transcript_timing_issues", []))
    result.update({"noise_db": -50, "minimum_quiet_us": 2000000, "margin_us": 500000, "quiet": quiet})
    return result


def filters(edit_plan, origin_us):
    spans = edit_plan["retained"]
    start = spans[0]["source_start_us"]
    end = spans[-1]["source_end_us"]
    keep = "+".join(
        f"gte(t,{(s['source_start_us'] - start) / 1e6:.6f})*lt(t,{(s['source_end_us'] - start) / 1e6:.6f})"
        for s in spans
    )
    shifts = (
        "+".join(f"{(b - a) / 1e6:.6f}*gte(T,{(b - start) / 1e6:.6f})" for a, b in edit_plan["removed"])
        or "0"
    )
    # Compress the video timeline once; concatenating A/V segments pads each audio seam to a frame.
    parts = [
        f"[0:v]trim=start={(start - origin_us) / 1e6:.6f}:duration={(end - start) / 1e6:.6f},setpts=PTS-STARTPTS,select='{keep}',setpts='PTS-({shifts})/TB'[cutv]"
    ]
    if len(spans) > 1:
        parts.append(f"[0:a]asplit={len(spans)}" + "".join(f"[sa{i}]" for i in range(len(spans))))
    for i, span in enumerate(spans):
        a = (span["source_start_us"] - origin_us) / 1e6
        duration = (span["source_end_us"] - span["source_start_us"]) / 1e6
        ainput = f"sa{i}" if len(spans) > 1 else "0:a"
        fade = ""
        if i:
            fade += "afade=t=in:d=0.005,"
        if i < len(spans) - 1:
            fade += f"afade=t=out:st={duration - 0.005:.6f}:d=0.005,"
        parts.append(
            f"[{ainput}]atrim=start={a:.6f}:duration={duration:.6f},asetpts=PTS-STARTPTS,{fade}aresample=48000[pa{i}]"
        )
    if len(spans) > 1:
        parts.append("".join(f"[pa{i}]" for i in range(len(spans))) + f"concat=n={len(spans)}:v=0:a=1[a]")
    else:
        parts.append("[pa0]anull[a]")
    return ";".join(parts) + ";"
