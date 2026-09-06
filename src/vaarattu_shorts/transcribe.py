from __future__ import annotations

import json
import hashlib
import sys
import time

from .contracts import Word
from .models import model_path
from .processes import lock, run_tool
from .storage import atomic_json, digest
from .youtube import pcm


def merge_chunks(chunks, duration_us):
    merged = []
    for chunk, result in chunks:
        for value in result["words"]:
            start = round(value["start"] * 1e6) + chunk["offset_us"]
            end = round(value["end"] * 1e6) + chunk["offset_us"]
            midpoint = (start + end) // 2
            if not chunk["core_start_us"] <= midpoint < chunk["core_end_us"]:
                continue
            start, end = max(0, start), min(duration_us, end)
            if end <= start or not value["text"].strip():
                continue
            merged.append(
                Word(
                    id="pending",
                    start_us=start,
                    end_us=end,
                    text=value["text"].strip(),
                    probability=value.get("probability"),
                )
            )
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
    fingerprint = hashlib.sha256(
        (
            digest(source)
            + digest(settings.models / profile / "manifest.json")
            + "fi-fp16-beam5-vad-unconditioned-core1200-overlap5-v1"
        ).encode()
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
        atomic_json(request, {"model": str(model), "chunks": chunks, "fingerprint": fingerprint})
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
