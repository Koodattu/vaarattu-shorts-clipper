import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaarattu_shorts import asr_child, transcribe
from vaarattu_shorts.config import load_settings
from vaarattu_shorts.pipeline import Pipeline
from vaarattu_shorts.storage import atomic_json, digest


@pytest.mark.parametrize("batch_size,flash,language", [(0, False, "fi"), (16, False, "fi"), (32, True, "fi"), (0, False, "en")])
def test_asr_child_routes_batching_and_retains_word_timing(tmp_path, monkeypatch, batch_size, flash, language):
    calls = []
    segment = SimpleNamespace(
        start=1.25,
        end=1.75,
        text="moi",
        no_speech_prob=0.01,
        avg_logprob=-0.1,
        temperature=0.0,
        words=[SimpleNamespace(start=1.25, end=1.75, word="moi", probability=0.99)],
    )

    class Model:
        def __init__(self, path, **kwargs):
            calls.append(("load", kwargs))
            self.model = SimpleNamespace(device="cuda", compute_type="float16")

        def transcribe(self, audio, **kwargs):
            calls.append(("unbatched", kwargs))
            return iter([segment]), SimpleNamespace(language="fi", language_probability=1,
                                                    duration=1200, duration_after_vad=1100)

    class Batched:
        def __init__(self, model):
            calls.append(("wrap", {}))

        def transcribe(self, audio, **kwargs):
            calls.append(("batched", kwargs))
            return iter([segment]), SimpleNamespace(language="fi", language_probability=1,
                                                    duration=1200, duration_after_vad=1100)

    monkeypatch.setitem(
        sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model, BatchedInferencePipeline=Batched)
    )
    request = tmp_path / "request.json"
    saved = tmp_path / "saved.json"
    output = tmp_path / "chunk.json"
    atomic_json(saved, {"fingerprint": "fixture", "words": []})
    atomic_json(
        request,
        {
            "model": "local-model",
            "fingerprint": "fixture",
            "batch_size": batch_size,
            "flash_attention": flash,
            "chunks": [
                {"audio": "already-done.wav", "output": str(saved)},
                {"audio": "pending.wav", "output": str(output), **({"language": "en"} if language == "en" else {})},
            ],
        },
    )
    monkeypatch.setattr(sys, "argv", ["asr_child", str(request)])
    asr_child.main()
    assert calls[0][1]["device"] == "cuda" and calls[0][1]["compute_type"] == "float16"
    assert calls[0][1]["local_files_only"] is True
    assert calls[0][1].get("flash_attention", False) is flash
    assert calls[-1][0] == ("batched" if batch_size else "unbatched")
    opts = calls[-1][1]
    assert opts["language"] == language and opts["word_timestamps"] is True
    assert opts["task"] == "transcribe"
    assert opts["vad_filter"] is True and opts["beam_size"] == 5
    assert opts["condition_on_previous_text"] is False
    assert opts.get("batch_size", 0) == batch_size
    assert len(calls) == (3 if batch_size else 2)
    result = json.loads(output.read_text())
    assert result["words"][0]["start"] == 1.25 and result["words"][0]["end"] == 1.75
    assert result["timing"]["audio_seconds"] == 1200
    assert result["timing"]["elapsed_seconds"] > 0
    assert result["duration_after_vad"] == 1100
    assert result["segments"][0]["temperature"] == 0.0
    assert json.loads((tmp_path / "runtime.json").read_text())["batch_size"] == batch_size


def test_batch_fingerprint_invalidates_only_changed_decode_settings(settings, monkeypatch):
    source = settings.work / "audio.opus"
    source.write_bytes(b"fixture audio")
    model = settings.models / "turbo"
    model.mkdir()
    atomic_json(model / "manifest.json", {"fixture": True})
    monkeypatch.setattr(transcribe, "model_path", lambda *_args, **_kwargs: model)
    calls = []

    def pcm(settings, source, output, *args, **kwargs):
        output.write_bytes(b"fixture PCM")

    def run_tool(args, *unused, **kwargs):
        req = json.loads(args[-1].read_text())
        calls.append(req)
        for chunk in req["chunks"]:
            atomic_json(
                Path(chunk["output"]),
                {
                    "fingerprint": req["fingerprint"],
                    "words": [{"start": 1, "end": 2, "text": "sana"}],
                },
            )

    monkeypatch.setattr(transcribe, "pcm", pcm)
    monkeypatch.setattr(transcribe, "run_tool", run_tool)
    folder = settings.work / "asr"
    legacy = replace(settings, asr_batch_size=0)
    for profile in (legacy, legacy, settings, settings, replace(settings, asr_flash_attention=True)):
        transcribe.transcribe(profile, source, 600, "turbo", folder, lambda: None, lambda _: None)
    assert len(calls) == 3
    original = hashlib.sha256(
        (
            digest(source)
            + digest(model / "manifest.json")
            + "fi-fp16-beam5-vad-unconditioned-core1200-overlap5-v1"
        ).encode()
    ).hexdigest()
    assert calls[0]["fingerprint"] == original
    assert len({r["fingerprint"] for r in calls}) == 3
    assert [r["batch_size"] for r in calls] == [0, 16, 16]


def test_legacy_runs_keep_unbatched_and_new_runs_keep_snapshot(settings, store):
    old = store.admit({}, "old")
    assert Pipeline(settings, store, old).settings.asr_batch_size == 0
    store.update(old, state="completed")
    new = store.admit({"asr_batch_size": 16, "asr_flash_attention": True}, "new")
    saved = Pipeline(replace(settings, asr_batch_size=32), store, new).settings
    assert saved.asr_batch_size == 16 and saved.asr_flash_attention is True


@pytest.mark.parametrize("overlap_us", [230000, 1500000])
def test_retry_merges_cached_segment_overlap_without_decoding(settings, monkeypatch, overlap_us):
    source = settings.work / "audio.opus"
    source.write_bytes(b"fixture audio")
    model = settings.models / "turbo"
    model.mkdir()
    atomic_json(model / "manifest.json", {"fixture": True})
    monkeypatch.setattr(transcribe, "model_path", lambda *_args, **_kwargs: model)
    fingerprint = hashlib.sha256(
        (
            digest(source)
            + digest(model / "manifest.json")
            + "fi-fp16-beam5-vad-unconditioned-core1200-overlap5-v1-batch16-flash0-v2"
        ).encode()
    ).hexdigest()
    folder = settings.work / "asr"
    folder.mkdir()
    cached = folder / "chunk-0.json"
    atomic_json(
        cached,
        {
            "fingerprint": fingerprint,
            "words": [
                {"start": 560, "end": 569.07, "text": "oma"},
                {"start": 569.07 - overlap_us / 1e6, "end": 569.84, "text": "näkemys"},
            ],
            "segments": [{"start": 527.15, "end": 569.07}, {"start": 568.84, "end": 599}],
        },
    )
    before = digest(cached)

    def forbidden(*args, **kwargs):
        pytest.fail("Saved transcription must not be decoded or inferred again")

    monkeypatch.setattr(transcribe, "pcm", forbidden)
    monkeypatch.setattr(transcribe, "run_tool", forbidden)
    result = transcribe.transcribe(settings, source, 600, "turbo", folder, lambda: None, lambda _: None)
    assert len(result["words"]) == 2 and result["timing_issues"][0]["overlap_us"] == overlap_us
    assert json.loads((folder / "transcript.json").read_text("utf-8")) == result
    assert digest(cached) == before


@pytest.mark.parametrize(
    "config",
    ["asr_batch_size = -1", "asr_batch_size = 65", "asr_batch_size = true", 'asr_flash_attention = "true"'],
)
def test_invalid_asr_config_is_rejected(tmp_path, config):
    (tmp_path / "config.toml").write_text(config)
    with pytest.raises(ValueError, match="asr_"):
        load_settings(tmp_path)
