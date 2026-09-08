from __future__ import annotations

import json
import hashlib
import sys
import time
import re
from pathlib import Path

from .contracts import MAX_CLIP_US, Word
from .models import model_path
from .processes import run_tool, waiting_lock
from .storage import atomic_json, digest
from .youtube import pcm


class SeamConflict(ValueError):
    """Two decoded contexts cannot be joined with a reliable shared phrase."""


def seam_handoff(left, right, boundary, conflict_start):
    candidates = []

    def nearby(words):
        return [
            (i, w)
            for i, w in enumerate(words)
            if boundary - 5000000 <= (w.start_us + w.end_us) // 2 <= boundary + 5000000
        ]

    left_near, right_near = nearby(left), nearby(right)
    for a in range(2, len(left_near)):
        left_phrase = left_near[a - 2 : a + 1]
        tokens = [re.sub(r"\W", "", w.text.casefold()) for _, w in left_phrase]
        if not all(tokens) or len(set(tokens)) < 2:
            continue
        for b in range(2, len(right_near)):
            right_phrase = right_near[b - 2 : b + 1]
            if tokens != [re.sub(r"\W", "", w.text.casefold()) for _, w in right_phrase]:
                continue
            if any(
                max(abs(lw.start_us - rw.start_us), abs(lw.end_us - rw.end_us)) > 300000
                for (_, lw), (_, rw) in zip(left_phrase, right_phrase)
            ):
                continue
            li, last = left_phrase[-1]
            ri = right_phrase[-1][0] + 1
            if ri >= len(right) or not 0 <= right[ri].start_us - last.end_us <= 1000000:
                continue
            # Prefer a handoff before the disputed phrase, retaining one decoder's full version.
            candidates.append((last.end_us > conflict_start, abs(last.end_us - boundary), li + 1, ri))
    if not candidates:
        raise SeamConflict(
            "The transcription chunks disagree at a boundary and have no reliable shared phrase."
        )
    _, _, left_stop, right_start = min(candidates)
    return left_stop, right_start


def merge_chunks(chunks, duration_us, timing_issues=None):
    contexts, selections = [], []
    origins = {}
    if timing_issues is None:
        timing_issues = []
    for chunk_index, (chunk, result) in enumerate(chunks):
        context, owned = [], []
        for value in result["words"]:
            start = round(value["start"] * 1e6) + chunk["offset_us"]
            end = round(value["end"] * 1e6) + chunk["offset_us"]
            midpoint = (start + end) // 2
            start, end = max(0, start), min(duration_us, end)
            if end <= start or not value["text"].strip():
                continue
            if chunk["core_start_us"] <= midpoint < chunk["core_end_us"]:
                owned.append(len(context))
            context.append(
                Word(
                    id="pending",
                    start_us=start,
                    end_us=end,
                    text=value["text"].strip(),
                    probability=value.get("probability"),
                )
            )
        origins.update((id(word), chunk_index) for word in context)
        contexts.append(context)
        selections.append([owned[0], owned[-1] + 1] if owned else [0, 0])
    for i in range(1, len(contexts)):
        left_start, left_stop = selections[i - 1]
        right_start, right_stop = selections[i]
        if left_start == left_stop or right_start == right_stop:
            continue
        left, right = contexts[i - 1], contexts[i]
        previous, following = left[left_stop - 1], right[right_start]
        if previous.end_us - following.start_us <= 150000:
            continue
        if (
            previous.text.casefold() == following.text.casefold()
            and abs(previous.start_us - following.start_us) < 300000
        ):
            continue
        boundary = chunks[i][0]["core_start_us"]
        try:
            stop, start = seam_handoff(left, right, boundary, previous.start_us)
            if stop <= left_start or start >= right_stop:
                raise SeamConflict("The shared phrase falls outside the owned chunks.")
        except SeamConflict:
            # Keep owned speech and flag the entire disputed context instead of aborting the VOD.
            timing_issues.append(
                {
                    "kind": "chunk_seam_conflict",
                    "chunk_indices": [i - 1, i],
                    "start_us": max(0, min(boundary - 5000000, previous.start_us, following.start_us)),
                    "end_us": min(duration_us, max(boundary + 5000000, previous.end_us, following.end_us)),
                }
            )
            continue
        selections[i - 1][1], selections[i][0] = stop, start
    merged = [word for context, (start, stop) in zip(contexts, selections) for word in context[start:stop]]
    merged.sort(key=lambda word: (word.start_us, word.end_us))
    words = []
    furthest = None
    for word in merged:
        if words and word.start_us < words[-1].end_us:
            previous = words[-1]
            if (
                origins[id(word)] != origins[id(previous)]
                and word.text.casefold() == previous.text.casefold()
                and abs(word.start_us - previous.start_us) < 300000
            ):
                continue
        word.id = f"w_{len(words):07d}"
        if furthest is not None and furthest.end_us - word.start_us > 150000:
            timing_issues.append(
                {
                    "kind": "word_overlap",
                    "chunk_indices": sorted({origins[id(furthest)], origins[id(word)]}),
                    "word_ids": [furthest.id, word.id],
                    "start_us": min(furthest.start_us, word.start_us),
                    "end_us": max(furthest.end_us, word.end_us),
                    "overlap_us": min(furthest.end_us, word.end_us) - word.start_us,
                }
            )
        if furthest is None or word.end_us > furthest.end_us:
            furthest = word
        words.append(word)
    return words


def transcribe(settings, source, duration, profile, folder, check, progress):
    model = model_path(settings, profile, verify=True)
    folder.mkdir(parents=True, exist_ok=True)
    duration_us = round(duration * 1e6)
    decoding = "fi-fp16-beam5-vad-unconditioned-core1200-overlap5-v1"
    if settings.asr_batch_size or settings.asr_flash_attention:
        decoding += f"-batch{settings.asr_batch_size}-flash{int(settings.asr_flash_attention)}-v2"
    fingerprint = hashlib.sha256(
        (digest(source) + digest(settings.models / profile / "manifest.json") + decoding).encode()
    ).hexdigest()
    core_us = 1200 * 1000000
    chunks = []
    for i, start in enumerate(range(0, duration_us, core_us)):
        check()
        end = min(start + core_us, duration_us)
        offset = max(0, start - 5000000)
        stop = min(duration_us, end + 5000000)
        audio = folder / f"chunk-{i}.wav"
        output = folder / f"chunk-{i}.json"
        if output.exists():
            try:
                cached = json.loads(output.read_text("utf-8"))
                if cached.get("fingerprint") != fingerprint:
                    output.unlink()
            except (ValueError, KeyError):
                output.unlink()
        if not output.exists() and audio.exists():
            audio.unlink()
        if not output.exists() and not audio.exists():
            pcm(settings, source, audio, offset / 1e6, (stop - offset) / 1e6, check)
        chunks.append(
            {
                "audio": str(audio),
                "output": str(output),
                "offset_us": offset,
                "core_start_us": start,
                "core_end_us": end,
            }
        )
        progress(i / max(1, (duration_us + core_us - 1) // core_us) * 0.15)
    with waiting_lock(settings.work / "gpu.lock", "Local\\VaarattuShortsGpu", check):
        request = folder / "asr-request.json"
        atomic_json(
            request,
            {
                "model": str(model),
                "chunks": chunks,
                "fingerprint": fingerprint,
                "batch_size": settings.asr_batch_size,
                "flash_attention": settings.asr_flash_attention,
            },
        )
        updated = 0.0

        def checkpoint_progress():
            nonlocal updated
            check()
            if time.monotonic() - updated >= 2:
                complete = sum((folder / f"chunk-{i}.json").exists() for i in range(len(chunks)))
                progress(0.15 + 0.85 * complete / max(1, len(chunks)))
                updated = time.monotonic()

        if any(not (folder / f"chunk-{i}.json").exists() for i in range(len(chunks))):
            run_tool(
                [sys.executable, "-m", "vaarattu_shorts.asr_child", request],
                settings,
                folder,
                "asr",
                checkpoint_progress,
                timeout=max(7200, duration * 2),
            )
    # OwnedProcess has waited for ASR exit before the GPU lock leaves this scope.
    results = [
        (chunk, json.loads((folder / f"chunk-{i}.json").read_text("utf-8"))) for i, chunk in enumerate(chunks)
    ]
    timing_issues = []
    words = merge_chunks(results, duration_us, timing_issues)
    transcript = {
        "schema_version": 1,
        "duration_us": duration_us,
        "profile": profile,
        "decoding": {
            "batch_size": settings.asr_batch_size,
            "flash_attention": settings.asr_flash_attention,
            "device": "cuda",
            "compute_type": "float16",
            "beam_size": 5,
        },
        "model_manifest": json.loads((settings.models / profile / "manifest.json").read_text("utf-8")),
        "words": [w.model_dump() for w in words],
        "timing_issues": timing_issues,
        "coverage": [[0, duration_us]],
        "chunks": [{k: v for k, v in c.items() if k != "audio"} for c in chunks],
    }
    atomic_json(folder / "transcript.json", transcript)
    for chunk in chunks:
        Path(chunk["audio"]).unlink(missing_ok=True)
    return transcript


def refine_clips(settings, source, duration_us, clips, folder, check, progress):
    """Decode selected source sections in one finite, unbatched large-v3 process."""
    if not clips:
        return {}
    model = model_path(settings, "large-v3", verify=True)
    manifest_hash = digest(settings.models / "large-v3" / "manifest.json")
    fingerprint = hashlib.sha256(
        (digest(source) + manifest_hash + "final-fi-fp16-beam5-vad-unconditioned-context10-v1").encode()
    ).hexdigest()
    folder = folder / fingerprint
    folder.mkdir(parents=True, exist_ok=True)
    chunks = []
    for clip in clips:
        check()
        body = clip["body"]
        start = max(0, body["start_us"] - 10000000)
        end = min(duration_us, body["end_us"] + 10000000)
        stem = f"{clip['id']}-{start}-{end}"
        audio, output = folder / f"{stem}.wav", folder / f"{stem}.json"
        if output.exists():
            try:
                cached = json.loads(output.read_text("utf-8"))
                if cached.get("fingerprint") != fingerprint or not isinstance(cached.get("words"), list):
                    output.unlink()
            except ValueError:
                output.unlink()
        if not output.exists():
            audio.unlink(missing_ok=True)
            pcm(settings, source, audio, start / 1e6, (end - start) / 1e6, check)
        chunks.append(
            {
                "clip_id": clip["id"],
                "audio": str(audio),
                "output": str(output),
                "offset_us": start,
                "core_start_us": start,
                "core_end_us": end,
            }
        )
    missing = [c for c in chunks if not Path(c["output"]).exists()]
    if missing:
        with waiting_lock(settings.work / "gpu.lock", "Local\\VaarattuShortsGpu", check):
            request = folder / "asr-request.json"
            atomic_json(
                request,
                {
                    "model": str(model),
                    "profile": "large-v3",
                    "chunks": missing,
                    "fingerprint": fingerprint,
                    "batch_size": 0,
                    "flash_attention": False,
                },
            )

            def checkpoint_progress():
                check()
                progress(sum(Path(c["output"]).exists() for c in chunks) / len(chunks))

            run_tool(
                [sys.executable, "-m", "vaarattu_shorts.asr_child", request],
                settings,
                folder,
                "final-asr",
                checkpoint_progress,
                timeout=max(600, sum(c["core_end_us"] - c["offset_us"] for c in missing) / 1e6 * 2),
            )
    # run_tool waits for the owned child to exit before releasing the GPU lock.
    results = {}
    for chunk in chunks:
        check()
        output = Path(chunk["output"])
        raw = json.loads(output.read_text("utf-8"))
        if raw.get("fingerprint") != fingerprint:
            raise ValueError("The final transcription cache changed. Retry the run.")
        timing_issues = []
        words = merge_chunks([(chunk, raw)], duration_us, timing_issues)
        for word in words:
            word.id = f"f_{chunk['clip_id']}_{word.id}"
        for issue in timing_issues:
            if "word_ids" in issue:
                issue["word_ids"] = [f"f_{chunk['clip_id']}_{key}" for key in issue["word_ids"]]
        results[chunk["clip_id"]] = {
            "profile": "large-v3",
            "model_manifest_sha256": manifest_hash,
            "fingerprint": fingerprint,
            "raw_sha256": digest(output),
            "start_us": chunk["core_start_us"],
            "end_us": chunk["core_end_us"],
            "words": [w.model_dump() for w in words],
            "timing_issues": timing_issues,
        }
        Path(chunk["audio"]).unlink(missing_ok=True)
    progress(1)
    return results


def apply_refinement(body, transcript):
    """Keep cuts whole-word without letting a new alignment substantially change the excerpt."""
    start, end = body["start_us"], body["end_us"]
    words = transcript["words"]
    # Expand over crossing words, including nested/overlapping word intervals.
    while True:
        crossing = [w for w in words if w["start_us"] < end and w["end_us"] > start]
        if not crossing:
            raise ValueError("Final transcription found no words in this clip. Check its source and timing.")
        a = min(start, min(w["start_us"] for w in crossing))
        b = max(end, max(w["end_us"] for w in crossing))
        if a == start and b == end:
            break
        start, end = a, b
    if (
        body["start_us"] - start > 500000
        or end - body["end_us"] > 500000
        or end - start > MAX_CLIP_US
        or start < transcript["start_us"]
        or end > transcript["end_us"]
    ):
        raise ValueError("Final word timing crosses this clip's cut. Adjust its boundaries before retrying.")
    return {
        **body,
        "start_us": start,
        "end_us": end,
        "words": [w for w in words if start <= w["start_us"] and w["end_us"] <= end],
        "caption_transcript": transcript,
        "discovery_bounds": {"start_us": body["start_us"], "end_us": body["end_us"]},
        "transcript_timing_issues": transcript["timing_issues"],
    }
