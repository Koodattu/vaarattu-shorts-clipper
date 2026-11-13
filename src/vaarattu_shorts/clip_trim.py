"""Manual clip edges using the saved source clock and canonical caption words."""
from __future__ import annotations

from . import pacing, tighten
from .contracts import MIN_CLIP_US, Word


def matches_source_timing(word, saved_start, saved_end, clip):
    # A caption may span the whole source word or just the part inside a previous exact trim.
    return (saved_start, saved_end) in (
        (word.start_us, word.end_us),
        (max(clip["start_us"], word.start_us), min(clip["end_us"], word.end_us)),
    )


def words_in_range(words, start, end):
    for word in words:
        left, right = max(start, word.start_us), min(end, word.end_us)
        if left < right:
            yield word.model_copy(update={"start_us": left, "end_us": right})


def context(body, transcript):
    mapping = body.get("mapping") or {}
    if "origin_us" not in mapping or not mapping.get("section_duration"):
        raise ValueError("This clip has no verified source timing. Re-render it before trimming.")
    captions = body.get("caption_transcript") or transcript
    minimum = max(0, mapping["origin_us"])
    maximum = min(transcript["duration_us"],
                  mapping["origin_us"] + round(mapping["section_duration"] * 1000000))
    if not minimum <= body["start_us"] < body["end_us"] <= maximum:
        raise ValueError("The saved source does not enclose this clip. Re-render it before trimming.")
    originals = list(captions["words"])
    if body.get("caption_transcript"):
        # Refined words stay canonical inside their coverage. The recording transcript
        # supplies the surrounding footage, without duplicating words at either seam.
        originals.extend(w for w in transcript["words"]
                         if w["end_us"] <= captions["start_us"] or w["start_us"] >= captions["end_us"])
    originals.sort(key=lambda w: (w["start_us"], w["end_us"]))
    edits = {w["id"]: w for w in body["words"]}
    words = []
    for original in originals:
        if original["end_us"] <= minimum or original["start_us"] >= maximum:
            continue
        word = Word.model_validate(original)
        saved = edits.get(word.id)
        if saved:
            if not matches_source_timing(word, saved["start_us"], saved["end_us"], body):
                raise ValueError("Saved captions no longer match their source timing. Check the clip before trimming.")
            word.text = saved["text"]
        words.append(word.model_dump())
    return dict(min_us=minimum, max_us=maximum, origin_us=mapping["origin_us"], words=words)


def snap(value, edge, words):
    # Repeat to handle overlapping ASR words without splitting either one.
    while True:
        crossing = [w for w in words if w["start_us"] < value < w["end_us"]]
        if not crossing:
            return value
        value = min(w["start_us"] for w in crossing) if edge == "start" else max(w["end_us"] for w in crossing)


def apply(body, available, start, end, *, snap_to_words=True):
    if not available["min_us"] <= start < end <= available["max_us"]:
        raise ValueError("Keep both edges inside the available source and caption range.")
    if snap_to_words:
        start, end = snap(start, "start", available["words"]), snap(end, "end", available["words"])
        if start < available["min_us"] or end > available["max_us"]:
            raise ValueError("Move this edge farther inside the available range to keep the whole word.")
    if (start, end) == (body["start_us"], body["end_us"]):
        raise ValueError("Move a clip edge before saving a new revision.")
    if end - start < MIN_CLIP_US:
        raise ValueError("Keep at least three seconds of the clip.")
    words = list(words_in_range(map(Word.model_validate, available["words"]), start, end))
    if not words:
        raise ValueError("Keep some speech in the selected range.")
    revised = {**body, "start_us": start, "end_us": end, "words": [w.model_dump() for w in words],
               "speech_cuts": [c for c in body.get("speech_cuts", []) if c["end_us"] > start and c["start_us"] < end]}
    captions = body.get("caption_transcript")
    if captions and (start < captions["start_us"] or end > captions["end_us"]):
        revised["caption_transcript"] = {**captions, "profile": "large-v3 + turbo",
                                         "start_us": available["min_us"], "end_us": available["max_us"],
                                         "words": available["words"]}
    try:
        cuts = tighten.intervals(revised)
    except ValueError as exc:
        raise ValueError("This edge crosses an existing speech cut or its neighboring words. "
                         "Move it past the shaded cut, or keep the speech on both sides.") from exc
    plan = pacing.plan(start, end, words, body.get("transcript_timing_issues", []),
                       enabled=body.get("trim_silence", False), speech_cuts=cuts)
    if not pacing.retime(words, plan):
        raise ValueError("Keep some speech outside the existing cuts.")
    revised.update(status="pending", folder=None, flags=[], reviewed=True, caption_check=None, tighten_check=None,
                   caption_correction_pass=body.get("caption_correction_pass") or "manual-trim",
                   pacing=plan, output_duration_us=plan["output_duration_us"],
                   # Undoing old cuts could restore speech outside the new excerpt.
                   speech_cut_history=[])
    if start < body["start_us"] or end > body["end_us"]:
        revised["context_expanded"] = True
    return revised
