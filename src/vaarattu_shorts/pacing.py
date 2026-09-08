"""Shorten internal transcript gaps while preserving word spans and speech margins."""

from __future__ import annotations

from .contracts import MIN_CLIP_US


def plan(start, end, words, timing_issues=(), *, enabled=True):
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
        if (
            not enabled
            or gap_end - gap_start < 1500000
            or any(i["start_us"] < gap_end and i["end_us"] > gap_start for i in timing_issues)
        ):
            continue
        # Keep 300 ms of breathing room before and after every transcript gap.
        removed.append([gap_start + 300000, gap_end - 300000])
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
    return {
        "version": 2,
        "method": "transcript_gaps",
        "minimum_gap_us": 1500000,
        "margin_us": 300000,
        "retained": spans,
        "removed": removed,
        "output_duration_us": duration,
    }


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
