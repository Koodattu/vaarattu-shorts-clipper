from contextlib import contextmanager

from vaarattu_shorts.pipeline import Pipeline
from vaarattu_shorts.storage import atomic_json


def test_one_run_reaches_output_with_adapters_replaced_and_resumes(settings, store, monkeypatch):
    """Exercise orchestration only: no media, ASR, LLM, subprocess or network execution."""
    events = []
    config = {
        "video": "abc_def-ghI",
        "model_manifests": {},
        "provider": "local",
        "budget_usd": 0,
        "max_clips": 3,
        "layout": {},
        "local_model": "gemma4-31b",
    }
    run = store.admit(config, "pipeline")
    store.claim()
    monkeypatch.setattr(
        "vaarattu_shorts.pipeline.youtube.metadata",
        lambda *_: {"id": "abc_def-ghI", "duration": 60, "title": "test", "upload_date": "20260709"},
    )

    def acquire(*args):
        folder = args[2]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "source.opus"
        path.write_bytes(b"fixture identity only")
        events.append("audio")
        return path

    monkeypatch.setattr("vaarattu_shorts.pipeline.youtube.acquire", acquire)
    monkeypatch.setattr("vaarattu_shorts.pipeline.youtube.probe", lambda *_: {"format": {"duration": "60"}})

    def transcribe(*args):
        events.extend(["asr-load", "asr-exit"])
        result = {"duration_us": 60000000, "words": []}
        atomic_json(args[4] / "transcript.json", result)
        return result

    monkeypatch.setattr("vaarattu_shorts.pipeline.transcribe.transcribe", transcribe)

    @contextmanager
    def server(*_):
        events.append("llm-load")
        try:
            yield None
        finally:
            events.append("llm-exit")

    monkeypatch.setattr("vaarattu_shorts.pipeline.local_server", server)

    def select(transcript, evaluator, progress, chat):
        assert chat == {"status": "unavailable"}
        assert events.index("chat-fetch") < events.index("llm-load")
        return {"verified": [], "coverage": [[0, 60000000]]}

    def enrich(*_):
        events.append("chat-fetch")
        return {"status": "unavailable"}

    monkeypatch.setattr("vaarattu_shorts.pipeline.discover.discover", select)
    monkeypatch.setattr("vaarattu_shorts.pipeline.stream_data.enrich", enrich)
    result = Pipeline(settings, store, run).execute()
    assert result["outcome"] == "no_candidates"
    assert (settings.ready / "runs" / f"{run}.json").is_file()
    assert events == ["audio", "asr-load", "asr-exit", "chat-fetch", "llm-load", "llm-exit"]
    assert store.get(run)["state"] == "completed"
    monkeypatch.setattr("vaarattu_shorts.pipeline.discover.VERSION", "later-version")
    assert Pipeline(settings, store, run).execute()["outcome"] == "no_candidates"
    assert len(events) == 6


def test_checkpoint_does_not_reuse_changed_artifact(settings, store):
    run = store.admit({"model_manifests": {}}, "hashes")
    pipeline = Pipeline(settings, store, run)
    file = settings.work / "artifact"
    calls = []

    def operation():
        calls.append(True)
        file.write_text("valid")
        return {"value": len(calls)}, [file]

    assert pipeline.stage("test", operation)["value"] == 1
    assert Pipeline(settings, store, run).stage("test", operation)["value"] == 1
    file.write_text("corrupt")
    assert Pipeline(settings, store, run).stage("test", operation)["value"] == 2
