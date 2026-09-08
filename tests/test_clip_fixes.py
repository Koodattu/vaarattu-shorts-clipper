import json
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import discover, render, youtube
from vaarattu_shorts.contracts import Candidate, Proposals, RunRequest, Word
from vaarattu_shorts.pipeline import Pipeline
from vaarattu_shorts.selection import ReviewPriority, prioritize
from vaarattu_shorts.web import create_app


def proposal(start, end):
    return Candidate(
        outcome="accept",
        start_word_id=f"w{start}",
        end_word_id=f"w{end}",
        idea_word_id=f"w{start}",
        category="story",
        summary_fi="Tarina",
        title_fi="Tarina",
        flags=[],
        reason="Ehjä tarina",
        scores=dict(standalone=4, opening=4, substance=4, payoff=4, fidelity=4),
    )


@pytest.mark.parametrize("leading_silence_us", [0, 800000])
@pytest.mark.parametrize("ranked", [False, True])
def test_all_proposals_are_verified_then_new_runs_are_capped_and_legacy_exports_preserved(
    settings, store, monkeypatch, leading_silence_us, ranked
):
    words = [
        Word(
            id=f"w{i}",
            start_us=i * 1000000 + leading_silence_us,
            end_us=(i + 1) * 1000000,
            text="Tarina.",
        )
        for i in range(360)
    ]
    candidates = [proposal(i * 15, i * 15 + 9) for i in range(20)]
    candidates[1] = proposal(3, 12)  # Distinct overlapping cuts both reach verification.
    candidates[-1].outcome = "reject"  # Discovery's recommendation is advisory too.
    calls = []

    class Evaluator:
        folder = settings.work
        discovery_budget = verification_budget = 1000000
        discovery_reasoning = verification_reasoning = "low"

        def check(self):
            pass

        def report(self, message):
            pass

        def request_size(self, *args):
            return 1

        def call(self, system, prompt, schema, step, *, validate=None, reasoning_effort=None):
            calls.append(step)
            if schema is ReviewPriority:
                result = schema(
                    candidates=[
                        {"candidate_id": c["candidate_id"], "recommendation": "review", "reason": "A story"}
                        for c in json.loads(prompt)
                    ]
                )
                validate(result)
                return result
            result = (
                Proposals(feedback="Fixture section feedback.", candidates=candidates)
                if schema == Proposals
                else candidates[int(step.split("-")[1])].model_copy(deep=True)
            )
            if schema is Candidate:
                index = int(step.split("-")[1])
                if index < 7:
                    result.outcome = "reject"
                elif index < 12:
                    result.scores.standalone = 2
            if validate:
                validate(result)
            return result

    timing_issues = [{"kind": "chunk_seam_conflict", "start_us": 1000000, "end_us": 5000000}]
    transcript = {
        "words": [w.model_dump() for w in words],
        "duration_us": 360000000,
        "timing_issues": timing_issues,
    }
    selection = discover.discover(transcript, Evaluator(), lambda _: None)
    assert len(calls) == 21 and len(selection["verified"]) == 20
    assert all(v["eligible"] for v in selection["verified"])
    assert all(v["review_notes"] for v in selection["verified"][:12])
    assert "max_clips" not in RunRequest.model_fields
    run = store.admit(
        {"video": "abc_def-ghI", "model_manifests": {}, "layout": {}, "max_clips": 3}, "all-clips"
    )
    pipeline = Pipeline(settings, store, run)
    values = {
        "metadata": {"id": "abc_def-ghI", "title": "Title", "upload_date": "20260709"},
        "audio": {"path": str(settings.work / "unused")},
        "transcript": transcript,
        "selection": selection,
        "chat": {"status": "unavailable"},
    }
    if ranked:
        values["review-priority"] = prioritize(selection, transcript["words"], Evaluator())
    else:
        # Completed v9 checkpoints keep their original uncapped delivery behavior.
        selection.pop("review_policy")
        selection["version"] = "conversation-v9"
    monkeypatch.setattr(pipeline, "stage", lambda name, *args: values[name])
    delivered = []

    def deliver(clip, *args):
        body = clip["body"]
        assert body["transcript_timing_issues"] == timing_issues
        speech_delay_us = body["words"][0]["start_us"] - body["start_us"]
        assert 0 <= speech_delay_us <= 500000
        assert speech_delay_us == min(leading_silence_us, 200000)
        delivered.append(clip["id"])
        store.save_clip(clip["id"], run, clip["revision"], {**clip["body"], "status": "ready"})

    monkeypatch.setattr(pipeline, "deliver", deliver)
    result = pipeline.execute()
    assert len(delivered) == len(result["ready"]) == (10 if ranked else 20)
    assert len(result["verified"]) == 20
    assert result["transcript_timing_issues"] == timing_issues


@pytest.mark.parametrize("drift", [False, True])
def test_alignment_decodes_section_once_and_tries_other_samples_after_silence(settings, monkeypatch, drift):
    rng = np.random.default_rng(42)
    reference = rng.integers(-3000, 3000, 180 * 2000, dtype=np.int16)
    section = reference[50 * 2000 : 100 * 2000].copy()
    section[42 * 2000 : 48 * 2000] = 0
    if drift:
        section[12 * 2000 : 20 * 2000] = reference[63 * 2000 : 71 * 2000]
    paths = settings.work / "full.opus", settings.work / "section.mkv"
    calls = []

    def pcm(settings, source, output, start, duration, check, rate):
        calls.append((source, start))
        data = section if source == paths[1] else reference
        begin = start or 0
        data = data[round(begin * rate) : round((begin + duration) * rate)]
        with wave.open(str(output), "wb") as w:
            w.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            w.writeframes(data.tobytes())

    monkeypatch.setattr(render, "pcm", pcm)
    monkeypatch.setattr(render, "probe", lambda *_: {"format": {"duration": "50"}})
    if drift:
        with pytest.raises(ValueError, match="drift apart"):
            render.align(settings, *paths, 50, settings.work, lambda: None)
    else:
        mapping = render.align(settings, *paths, 50, settings.work, lambda: None)
        assert mapping["origin_us"] == 50000000
        assert len(mapping["anchors"]) >= 2
    assert [start for path, start in calls if path == paths[1]] == [None]
    audit = json.loads((settings.work / "alignment.json").read_text("utf-8"))
    assert audit["unusable_samples"][0]["section_seconds"] == 42


def test_range_download_reencodes_at_cut_and_preserves_bounded_range(settings, monkeypatch):
    captured = []
    folder = settings.work / "section"

    def run(args, settings, folder, *_, **kwargs):
        captured.extend(args)
        output = folder / "source.mkv"
        output.write_bytes(b"fixture")
        (folder / "download.txt").write_text(str(output), encoding="utf-8")

    monkeypatch.setattr(youtube, "run_tool", run)
    monkeypatch.setattr(
        youtube, "probe", lambda *_: {"streams": [{"codec_type": "audio"}, {"codec_type": "video"}]}
    )
    youtube.acquire(settings, "abc_def-ghI", folder, lambda: None, (100, 145))
    assert captured[captured.index("--download-sections") + 1] == "*100.000000-145.000000"
    assert "--force-keyframes-at-cuts" in captured
    assert "-c:v libx264" in captured[captured.index("--downloader-args") + 1]


def test_continuous_pcm_decode_does_not_seek_even_to_zero(settings, monkeypatch):
    commands = []

    def run(args, *_, **kwargs):
        commands.append(args)
        args[-1].write_bytes(b"fixture")

    monkeypatch.setattr(youtube, "run_tool", run)
    youtube.pcm(
        settings,
        settings.work / "source.mkv",
        settings.work / "sample.wav",
        None,
        50,
        lambda: None,
        rate=2000,
    )
    assert "-ss" not in commands[0]


def test_render_rejects_missing_picture_at_clip_start(settings, monkeypatch):
    monkeypatch.setattr(
        render,
        "probe",
        lambda *_: {
            "format": {"start_time": "-0.007"},
            "streams": [{"codec_type": "video", "start_time": "2.566", "width": 1920, "height": 1080}],
        },
    )
    layout = {
        "name": "test",
        "camera": {"x": 0, "y": 0, "width": 0.3, "height": 0.3},
        "gameplay": {"x": 0, "y": 0, "width": 1, "height": 1},
    }
    with pytest.raises(ValueError, match="picture starts after"):
        render.render_clip(
            settings,
            settings.work / "source.mkv",
            {"origin_us": 0, "section_duration": 40},
            {"start_us": 0, "end_us": 10000000},
            layout,
            [],
            settings.work,
            lambda: None,
        )


@pytest.mark.parametrize("status", ["held", "ready"])
def test_retry_queues_media_only_and_preserves_review_and_revision_guards(settings, status):
    app = create_app(settings)
    store = app.state.store
    run = store.admit({}, "retry")
    store.update(run, state="completed")
    clip_id = "a" * 32
    body = {
        "status": status,
        "reviewed": False,
        "flags": ["Timing failed"],
        "start_us": 0,
        "end_us": 10000000,
    }
    store.save_clip(clip_id, run, 1, body)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        url = f"/api/clips/{clip_id}/retry"
        assert client.post(url, json={"expected_revision": 1}).status_code == 403
        assert client.post(url, json={"expected_revision": 2}, headers=headers).status_code == 400
        assert client.post(url, json={"expected_revision": 1}, headers=headers).status_code == 202
        assert client.post(url, json={"expected_revision": 1}, headers=headers).status_code == 400
    assert store.get(run)["stage"] == "rerender"
    current = store.clip(clip_id)
    assert current["revision"] == 2 and not current["body"]["reviewed"]
    assert current["body"]["start_us"] == 0 and current["body"]["status"] == "pending"
