import json

import pytest

from vaarattu_shorts.contracts import Word
from vaarattu_shorts.pipeline import Pipeline
from vaarattu_shorts.processes import Interrupted
from vaarattu_shorts.render import render_clip
from vaarattu_shorts.storage import atomic_json, digest


@pytest.mark.parametrize("duration", [8, 30])
@pytest.mark.parametrize("problem", ["caption", "transcript", "outside_clip"])
def test_render_holds_only_clips_with_technical_or_transcript_issues(
    settings, monkeypatch, duration, problem
):
    folder = settings.ready / ".staging" / "test"
    folder.mkdir(parents=True)
    source = settings.work / "source.mkv"
    source.write_bytes(b"fixture")
    commands = []

    def run(args, settings, folder, name, check, **kwargs):
        commands.append(args)
        check()
        (folder / f"{name}.log").write_text("", encoding="utf-8")
        if name == "render":
            (folder / "short.mp4").write_bytes(b"fixture output, not media")

    def probe(settings, source, folder, check):
        if source.name == "short.mp4":
            return {
                "format": {"duration": str(duration)},
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1080,
                        "height": 1920,
                        "pix_fmt": "yuv420p",
                        "duration": str(duration),
                    },
                    {"codec_type": "audio", "duration": str(duration)},
                ],
            }
        return {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}

    monkeypatch.setattr("vaarattu_shorts.render.run_tool", run)
    monkeypatch.setattr("vaarattu_shorts.render.probe", probe)
    layout = {
        "name": "checked",
        "camera": {"x": 0, "y": 0, "width": 0.3, "height": 0.3},
        "gameplay": {"x": 0.3, "y": 0.3, "width": 0.7, "height": 0.7},
        "calibrated": True,
        "solo_host": True,
    }
    words = [
        Word(
            id="w_0",
            start_us=0,
            end_us=1000000,
            text=("Liianpitkäyhdyssanajokaeimahduriville" if problem == "caption" else "Puhetta"),
        )
    ]
    issue_start = 2000000 if problem == "transcript" else duration * 1000000
    result = render_clip(
        settings,
        source,
        {"origin_us": 0, "section_duration": 40},
        {
            "start_us": 0,
            "end_us": duration * 1000000,
            "title": "Test",
            "reviewed": True,
            "transcript_timing_issues": [{"start_us": issue_start, "end_us": issue_start + 1000000}],
        },
        layout,
        words,
        folder,
        lambda: None,
    )
    assert result["status"] == ("ready" if problem == "outside_clip" else "held")
    assert bool(result["flags"]) == (problem != "outside_clip")
    if problem == "transcript":
        assert any("Transcription is uncertain" in flag for flag in result["flags"])
    graph = (folder / "filters.txt").read_text("utf-8")
    assert f"trim=start=0.000000:duration={duration:.6f}" in graph
    assert "vstack" in graph and "captions.ass" in graph
    assert any("libx264" in args for args in commands)


def test_promoted_package_is_reconciled_without_rerender(settings, store, monkeypatch):
    run = store.admit({"model_manifests": {}}, "recovery")
    clip_id = "a" * 32
    folder = settings.ready / clip_id / "1"
    folder.mkdir(parents=True)
    (folder / "short.mp4").write_bytes(b"immutable test artifact")
    body = {"start_us": 0, "end_us": 30000000, "title": "Title", "words": [], "layout": {}, "status": "ready"}
    atomic_json(folder / "metadata.json", {**body, "video_sha256": digest(folder / "short.mp4")})
    store.save_clip(clip_id, run, 1, {**body, "status": "pending"})

    def forbidden(*_):
        raise AssertionError("An already promoted valid package must not render again")

    monkeypatch.setattr("vaarattu_shorts.pipeline.render.render_clip", forbidden)
    Pipeline(settings, store, run).deliver(store.clip(clip_id), settings.work / "unused", 60000000)
    assert store.clip(clip_id)["body"]["status"] == "ready"
    (folder / "short.mp4").write_bytes(b"modified")
    with pytest.raises(ValueError, match="previous export differs"):
        Pipeline(settings, store, run).deliver(store.clip(clip_id), settings.work / "unused", 60000000)


def test_cancel_fences_stage_checkpoint(settings, store):
    run = store.admit({}, "cancel")
    store.claim()
    pipeline = Pipeline(settings, store, run)

    def operation():
        store.control(run, "cancel")
        return {}, []

    with pytest.raises(Interrupted):
        pipeline.stage("cancelled", operation)
    assert not (pipeline.folder / "cancelled.checkpoint.json").exists()


def test_changed_upstream_checkpoint_invalidates_downstream(settings, store):
    run = store.admit({}, "chain")
    file = settings.work / "input"
    file.write_text("first")
    pipeline = Pipeline(settings, store, run)
    pipeline.stage("upstream", lambda: ({"version": 1}, [file]))
    pipeline.stage("downstream", lambda: ({"version": 1}, []))
    file.write_text("second")
    pipeline = Pipeline(settings, store, run)
    pipeline.stage("upstream", lambda: ({"version": 2}, [file]))
    value = pipeline.stage("downstream", lambda: ({"version": 2}, []))
    assert value["version"] == 2
    assert json.loads((pipeline.folder / "downstream.checkpoint.json").read_text())["input_chain"]
