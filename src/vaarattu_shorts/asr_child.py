"""Finite ASR process. Only invoked explicitly by the worker, never at import."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .storage import atomic_json


def main():
    request = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    from faster_whisper import WhisperModel

    model = WhisperModel(request["model"], device="cuda", compute_type="float16", local_files_only=True)
    for chunk in request["chunks"]:
        output = Path(chunk["output"])
        if (
            output.exists()
            and json.loads(output.read_text("utf-8")).get("fingerprint") == request["fingerprint"]
        ):
            continue
        segments, info = model.transcribe(
            chunk["audio"],
            language="fi",
            word_timestamps=True,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
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
        atomic_json(
            output,
            {
                "words": words,
                "segments": diagnostics,
                "language": info.language,
                "fingerprint": request["fingerprint"],
            },
        )


if __name__ == "__main__":
    main()
