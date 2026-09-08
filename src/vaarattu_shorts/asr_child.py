"""Finite ASR process. Only invoked explicitly by the worker, never at import."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .storage import atomic_json


def main():
    request = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    batch_size = request.get("batch_size", 0)
    flash = request.get("flash_attention", False)
    started = time.perf_counter()
    model = WhisperModel(
        request["model"],
        device="cuda",
        compute_type="float16",
        local_files_only=True,
        **({"flash_attention": True} if flash else {}),
    )
    runtime = {
        "device": model.model.device,
        "compute_type": model.model.compute_type,
        "batch_size": batch_size,
        "flash_attention_requested": flash,
        "load_seconds": time.perf_counter() - started,
    }
    if runtime["device"] != "cuda" or runtime["compute_type"] != "float16":
        raise RuntimeError("The transcription model did not load on CUDA with FP16. No fallback was used.")
    atomic_json(Path(sys.argv[1]).parent / "runtime.json", runtime)
    profile = request.get("profile", "turbo")
    print(f"{profile} ready: CUDA FP16, batch_size={batch_size}, flash_attention={flash}", flush=True)
    transcriber = BatchedInferencePipeline(model=model) if batch_size else model
    for chunk in request["chunks"]:
        output = Path(chunk["output"])
        if (
            output.exists()
            and json.loads(output.read_text("utf-8")).get("fingerprint") == request["fingerprint"]
        ):
            continue
        started = time.perf_counter()
        segments, info = transcriber.transcribe(
            chunk["audio"],
            language="fi",
            word_timestamps=True,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
            **({"batch_size": batch_size} if batch_size else {}),
        )
        words, diagnostics = [], []
        for segment in segments:
            diagnostics.append(
                {
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text,
                    "no_speech_prob": segment.no_speech_prob,
                    "avg_logprob": segment.avg_logprob,
                }
            )
            for word in segment.words or []:
                words.append(
                    {"start": word.start, "end": word.end, "text": word.word, "probability": word.probability}
                )
        elapsed = time.perf_counter() - started
        timing = {
            "elapsed_seconds": elapsed,
            "audio_seconds": info.duration,
            "audio_seconds_per_second": info.duration / elapsed if elapsed > 0 else None,
        }
        atomic_json(
            output,
            {
                "words": words,
                "segments": diagnostics,
                "language": info.language,
                "fingerprint": request["fingerprint"],
                "runtime": runtime,
                "timing": timing,
            },
        )
        print(f"{output.stem}: {info.duration:.1f}s audio in {elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
