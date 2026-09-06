from __future__ import annotations

import json
import hashlib
import sys
import time
import re

from .contracts import Word
from .models import model_path
from .processes import lock, run_tool
from .storage import atomic_json, digest
from .youtube import pcm


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
        raise ValueError(
            "The transcription chunks disagree at a boundary and have no reliable shared phrase."
        )
    _, _, left_stop, right_start = min(candidates)
    return left_stop, right_start


def merge_chunks(chunks, duration_us):
    contexts, selections = [], []
    for chunk, result in chunks:
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
        stop, start = seam_handoff(left, right, chunks[i][0]["core_start_us"], previous.start_us)
        if stop <= left_start or start >= right_stop:
            raise ValueError("The transcription boundary cannot be reconciled within its owned chunks.")
        selections[i - 1][1], selections[i][0] = stop, start
    merged = [word for context, (start, stop) in zip(contexts, selections) for word in context[start:stop]]
    merged.sort(key=lambda word: (word.start_us, word.end_us))
    words = []
    for word in merged:
        if words and word.start_us < words[-1].end_us:
            previous = words[-1]
            if (
                word.text.casefold() == previous.text.casefold()
                and abs(word.start_us - previous.start_us) < 300000
            ):
                continue
            # Preserve uncertain overlap as an explicit failure; do not invent timing.
            if previous.end_us - word.start_us > 150000:
                raise ValueError("Overlapping speech at a transcription seam needs attention.")
        word.id = f"w_{len(words):07d}"
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
    with lock(settings.work / "gpu.lock", "Local\\VaarattuShortsGpu"):
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
    words = merge_chunks(results, duration_us)
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
        "coverage": [[0, duration_us]],
        "chunks": [{k: v for k, v in c.items() if k != "audio"} for c in chunks],
    }
    atomic_json(folder / "transcript.json", transcript)
    for chunk in chunks:
        from pathlib import Path

        Path(chunk["audio"]).unlink(missing_ok=True)
    return transcript
