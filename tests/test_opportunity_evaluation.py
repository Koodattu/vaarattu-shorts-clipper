import json

import httpx
import pytest

from vaarattu_shorts import discover, evaluation
from vaarattu_shorts.contracts import Candidate, Proposals, RunRequest, Word
from vaarattu_shorts.llm import Evaluator
from vaarattu_shorts.storage import atomic_json, digest


def candidate():
    return Candidate(
        outcome="accept",
        start_word_id="w0",
        end_word_id="w0",
        idea_word_id="w0",
        category="joke",
        reason="Lyhyt vitsi.",
        flags=[],
        scores=dict(standalone=2, opening=2, substance=2, payoff=2, fidelity=4),
        title_fi="Vitsi",
        summary_fi="Lyhyt vitsi",
    )


@pytest.mark.parametrize("duration,eligible", [(2999999, False), (3000000, True), (4000000, True)])
def test_minimum_slim_discovery_and_separate_reasoning_are_enforced(
    settings, store, monkeypatch, duration, eligible
):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret")
    run = store.admit({}, "minimum")
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        value = (
            Proposals(candidates=[candidate()], feedback="Lyhyt vitsi.")
            if len(requests) == 1
            else candidate()
        )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": value.model_dump_json()}]}],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        evaluator = Evaluator(
            "openai",
            store,
            run,
            settings.work,
            1,
            lambda: None,
            client,
            discovery_reasoning="medium",
            verification_reasoning="low",
        )
        result = discover.discover(
            {
                "duration_us": duration,
                "words": [Word(id="w0", start_us=0, end_us=duration, text="Vitsi.").model_dump()],
            },
            evaluator,
            lambda _: None,
        )
    assert result["verified"][0]["eligible"] is eligible
    assert result["verified"][0]["end_us"] == duration  # No artificial duration padding.
    assert [r["reasoning"]["effort"] for r in requests] == ["medium"] + ["low"] * (1 if eligible else 2)
    definitions = requests[0]["text"]["format"]["schema"]["$defs"]["DiscoveryCandidate"]["properties"]
    assert "title_fi" not in definitions and "summary_fi" not in definitions
    artifacts = [json.loads(p.read_text("utf-8")) for p in settings.work.glob("*.request.json")]
    assert len(artifacts) == len(requests)
    assert sorted(map(json.dumps, artifacts)) == sorted(map(json.dumps, requests))
    assert "fixture-secret" not in json.dumps(artifacts)


def test_comparison_uses_identical_sections_and_isolated_ledger(settings, store, monkeypatch):
    run = store.admit(
        {
            "provider": "codex",
            "budget_usd": 0,
            "codex": {"model": "gpt-5.6-luna", "base_url": "http://localhost:18080/v1"},
        },
        "original",
    )
    transcript = {
        "duration_us": 720000000,
        "words": [Word(id="w0", start_us=0, end_us=4000000, text="Vitsi.").model_dump()],
    }
    path = settings.work / "runs" / run / "transcript.checkpoint.json"
    atomic_json(path, {"result": transcript})
    before = store.get(run)
    calls = []

    def compare(transcript, evaluator, progress, seed, regions):
        calls.append(
            (
                transcript,
                evaluator.run_id,
                evaluator.store.path,
                evaluator.discovery_reasoning,
                evaluator.verification_reasoning,
                regions,
                seed,
            )
        )
        return {
            "proposals": [candidate().model_dump()],
            "verified": [
                {"candidate": candidate().model_dump(), "eligible": True, "start_us": 0, "end_us": 4000000}
            ],
            "issues": [],
        }

    monkeypatch.setattr(discover, "discover", compare)
    folder = evaluation.compare_reasoning(settings, run, [0])
    assert calls[0][0:3] == calls[1][0:3]
    assert [(c[3], c[4]) for c in calls] == [("low", "low"), ("medium", "low")]
    assert calls[0][5] == calls[1][5] == [{"start_us": 0, "end_us": 360000000}]
    assert calls[0][2] != store.path and store.get(run) == before
    assert (folder / "medium/review.csv").is_file()
    assert json.loads((folder / "manifest.json").read_text())["transcript_sha256"] == digest(path)


def test_encoder_benchmark_does_not_queue_or_overwrite_clips(settings, store, monkeypatch):
    run = store.admit({}, "original")
    source = settings.work / "section.mkv"
    source.write_bytes(b"fixture")
    body = {
        "section": str(source),
        "section_sha256": digest(source),
        "mapping": {},
        "layout": {},
        "words": [],
        "status": "ready",
    }
    body["mapping"] = {"origin_us": 0}
    store.save_clip("clip", run, 3, body)
    calls = []

    def render(settings, section, mapping, body, layout, words, folder, check):
        calls.append(body["video_encoder"])
        if body["video_encoder"] == "h264_nvenc":
            raise ValueError("GPU unavailable")
        return {"encode_seconds": 1, "video_bytes": 100, "output_duration_us": 4000000, "flags": []}

    monkeypatch.setattr(evaluation.render, "render_clip", render)
    folder = evaluation.benchmark_render(settings, "clip")
    assert calls == ["libx264", "h264_nvenc"]
    result = json.loads((folder / "comparison.json").read_text())
    assert [r["status"] for r in result["results"]] == ["passed", "failed"]
    assert store.clip("clip")["body"] == body and store.clip("clip")["revision"] == 3


def test_new_run_defaults_and_settings_validation():
    request = RunRequest(video="abc_def-ghI", layout_id="a" * 32)
    assert request.trim_silence and request.video_encoder == "h264_nvenc"
    assert request.discovery_reasoning == request.verification_reasoning == "low"
    with pytest.raises(ValueError):
        RunRequest(video="abc_def-ghI", layout_id="a" * 32, discovery_reasoning="invented")
