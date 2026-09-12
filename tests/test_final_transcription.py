import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import pipeline, transcribe
from vaarattu_shorts.contracts import RunRequest
from vaarattu_shorts.processes import Interrupted
from vaarattu_shorts.storage import atomic_json, digest
from vaarattu_shorts.web import create_app


def word(start=11000000, end=12000000, text="Korjattu", id="f_word"):
    return dict(id=id, start_us=start, end_us=end, text=text, probability=0.99)


def captions(words=None):
    return dict(
        profile="large-v3",
        start_us=0,
        end_us=30000000,
        words=[word()] if words is None else words,
        timing_issues=[],
    )


def clip_body():
    return dict(
        start_us=10000000,
        end_us=20000000,
        words=[word(text="Turbo", id="w0")],
        title="Tarina",
        status="pending",
        layout={},
        reviewed=False,
    )


def prepared(settings, monkeypatch):
    model = settings.models / "large-v3"
    model.mkdir()
    atomic_json(model / "manifest.json", {"fixture": True})
    monkeypatch.setattr(transcribe, "model_path", lambda *args, **kw: model)
    return model


@pytest.mark.parametrize("text,language", [
    ("Tää buildi on ihan hyvä mutta loot oli taas huono ja sitten tuli boss fight", "fi"),
    ("Heavyweight sukulointi ja vähän socializing mutta ei tässä mitään muuta ole", "fi"),
    ("The Lord of the Rings", "fi"),
    ("Mä sanoin että The Lord of the Rings on hyvä mutta en ole katsonut sitä", "fi"),
    ("I would love to see that and it would have made the movie a five star film", "en"),
])
def test_caption_language_requires_sustained_english(text, language):
    assert transcribe.caption_language([word(text=text)]) == language


def test_refinement_rejects_english_translation_even_with_full_timing_coverage():
    original = word(text="I would love to see that and it would have made the movie a five star film")
    translated = word(text="Haluaisin nähdä sen ja se olisi tehnyt elokuvasta viiden tähden elokuvan")
    with pytest.raises(ValueError, match="changed the language"):
        transcribe.apply_refinement({**clip_body(), "words": [original]}, captions([translated]))
    preserved = word(text="I'd love to see that and it would have made this movie a five star film")
    assert transcribe.apply_refinement({**clip_body(), "words": [original]}, captions([preserved]))["words"] == [preserved]


@pytest.mark.parametrize("recover", [True, False])
def test_final_transcription_retries_only_invalid_clips_once(settings, monkeypatch, recover):
    prepared(settings, monkeypatch)
    source = settings.work / "source.opus"
    source.write_bytes(b"source")
    original = [word(t * 1000000, t * 1000000 + 700000, id=f"w{t}") for t in range(10, 20)]
    clips = [dict(id=key, body={**clip_body(), "words": original}) for key in ("good", "bad")]
    requests = []

    @contextmanager
    def lock(*args):
        yield

    def child(args, *a, **kw):
        request = json.loads(args[-1].read_text())
        requests.append(request)
        for chunk in request["chunks"]:
            retry = chunk["offset_us"] == 10000000
            words = original if chunk["clip_id"] == "good" or (retry and recover) else original[-1:]
            atomic_json(Path(chunk["output"]), dict(fingerprint=request["fingerprint"], words=[
                dict(start=(w["start_us"] - chunk["offset_us"]) / 1e6,
                     end=(w["end_us"] - chunk["offset_us"]) / 1e6, text=w["text"])
                for w in words
            ]))

    monkeypatch.setattr(transcribe, "waiting_lock", lock)
    monkeypatch.setattr(transcribe, "pcm", lambda settings, source, output, *a: output.write_bytes(b"PCM"))
    monkeypatch.setattr(transcribe, "run_tool", child)
    args = (settings, source, 60000000, clips, settings.work / "final", lambda: None, lambda _: None)
    results = transcribe.refine_clips(*args)
    assert [[c["clip_id"] for c in r["chunks"]] for r in requests] == [["good", "bad"], ["bad"]]
    assert results["good"]["context_us"] == 1000000 and "retry_reason" not in results["good"]
    assert results["bad"]["context_us"] == 0 and "omitted speech" in results["bad"]["retry_reason"]
    if recover:
        assert len(transcribe.apply_refinement(clips[1]["body"], results["bad"])["words"]) == 10
    else:
        with pytest.raises(ValueError, match="omitted speech"):
            transcribe.apply_refinement(clips[1]["body"], results["bad"])
    assert transcribe.refine_clips(*args) == results
    assert len(requests) == 2


def test_final_transcription_is_optional_and_preflight_pins_only_requested_model(settings, monkeypatch):
    monkeypatch.setattr(pipeline, "check_provider", lambda *_: None)
    monkeypatch.setattr(pipeline.shutil, "which", lambda *_: "fixture")
    monkeypatch.setattr(pipeline.importlib.util, "find_spec", lambda *_: True)
    seen = []
    monkeypatch.setattr(pipeline, "model_path", lambda settings, key: seen.append(key))
    for key in ("turbo", "large-v3"):
        folder = settings.models / key
        folder.mkdir()
        atomic_json(folder / "manifest.json", {"key": key})
    request = RunRequest(video="abc_def-ghI", layout_id="a" * 32, provider="codex")
    assert request.final_transcription is False
    assert set(pipeline.preflight(settings, request)) == {"turbo"}
    assert seen == ["turbo"]
    request.final_transcription = True
    result = pipeline.preflight(settings, request)
    assert result["large-v3"] == digest(settings.models / "large-v3/manifest.json")
    assert seen == ["turbo", "turbo", "large-v3"]


def test_final_sections_share_one_child_and_resume_partial_cache(settings, monkeypatch):
    model = prepared(settings, monkeypatch)
    source = settings.work / "source.opus"
    source.write_bytes(b"audio identity")
    clips = [
        dict(id="a", body=clip_body()),
        dict(id="b", body={**clip_body(), "start_us": 50000000, "end_us": 60000000}),
    ]
    events, extracted, requests = [], [], []

    @contextmanager
    def lock(*args):
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    def pcm(settings, source, output, start, duration, check):
        extracted.append((start, duration))
        output.write_bytes(b"PCM")

    interrupt = True

    def child(args, settings, folder, label, check, **kwargs):
        nonlocal interrupt
        assert events[-1] == "lock"
        events.append("load")
        req = json.loads(args[-1].read_text())
        requests.append(req)
        try:
            for chunk in req["chunks"]:
                atomic_json(
                    Path(chunk["output"]),
                    {
                        "fingerprint": req["fingerprint"],
                        "words": [{"start": 2, "end": 3, "text": "Korjattu"}],
                    },
                )
                if interrupt:
                    interrupt = False
                    raise Interrupted("pause")
        finally:
            events.append("exit")

    monkeypatch.setattr(transcribe, "waiting_lock", lock)
    monkeypatch.setattr(transcribe, "pcm", pcm)
    monkeypatch.setattr(transcribe, "run_tool", child)
    args = (settings, source, 100000000, clips, settings.work / "final-asr", lambda: None, lambda _: None)
    with pytest.raises(Interrupted):
        transcribe.refine_clips(*args)
    result = transcribe.refine_clips(*args)
    assert [len(r["chunks"]) for r in requests] == [2, 1]
    assert extracted == [(9, 12), (49, 12), (49, 12)]
    assert events == ["lock", "load", "exit", "unlock"] * 2
    assert all(r["profile"] == "large-v3" and r["batch_size"] == 0 for r in requests)
    assert result["a"]["words"][0]["start_us"] == 11000000
    assert result["b"]["words"][0]["start_us"] == 51000000
    assert result["a"]["words"][0]["id"] != result["b"]["words"][0]["id"]
    assert transcribe.refine_clips(*args) == result
    assert len(requests) == 2
    assert not list((settings.work / "final-asr").rglob("*.wav"))
    atomic_json(model / "manifest.json", {"fixture": "new revision"})
    assert transcribe.refine_clips(*args)["a"]["fingerprint"] != result["a"]["fingerprint"]
    assert len(requests[-1]["chunks"]) == 2


def test_refinement_preserves_discovery_and_snaps_crossing_word():
    body = clip_body()
    new_words = [word(9900000, 10500000), word(19000000, 20100000, id="f2")]
    result = transcribe.apply_refinement(body, captions(new_words))
    assert (result["start_us"], result["end_us"]) == (9900000, 20100000)
    assert result["words"] == new_words
    assert result["discovery_bounds"] == dict(start_us=10000000, end_us=20000000)
    assert body["words"][0]["text"] == "Turbo"
    assert body["start_us"] == 10000000


@pytest.mark.parametrize("words", [[], [word(8000000, 11000000)], [word(19000000, 22000000)]])
def test_empty_or_large_boundary_disagreement_is_not_silently_rendered(words):
    with pytest.raises(ValueError):
        transcribe.apply_refinement(clip_body(), captions(words))


@pytest.mark.parametrize("missing_at", [10, 14, 17])
def test_refinement_rejects_missing_speech_at_start_middle_or_end(missing_at):
    original = [word(t * 1000000, t * 1000000 + 700000, id=f"w{t}") for t in range(10, 20)]
    refined = [w for i, w in enumerate(original, 10) if not missing_at <= i < missing_at + 3]
    body = {**clip_body(), "words": original}
    with pytest.raises(ValueError, match="omitted speech"):
        transcribe.apply_refinement(body, captions(refined))
    assert body["words"] == original


def test_refinement_allows_real_silence_and_changed_word_segmentation():
    original = [word(10000000, 10700000), word(18000000, 18700000, id="w2")]
    refined = [word(10200000, 10900000, text="Better wording"), word(18200000, 18900000, id="f2")]
    result = transcribe.apply_refinement({**clip_body(), "words": original}, captions(refined))
    assert result["words"] == refined


def test_final_pass_follows_ranking_exit_and_precedes_every_render(settings, store, monkeypatch):
    model = prepared(settings, monkeypatch)
    run = store.admit(
        dict(
            video="abc_def-ghI",
            provider="local",
            budget_usd=0,
            final_transcription=True,
            model_manifests={"large-v3": digest(model / "manifest.json")},
        ),
        "final",
    )
    for key in ("a", "b"):
        store.save_clip(key, run, 1, clip_body())
    events = []

    @contextmanager
    def server(*args):
        events.append("llm-load")
        try:
            yield None
        finally:
            events.append("llm-exit")

    monkeypatch.setattr(pipeline, "local_server", server)
    monkeypatch.setattr(pipeline, "Evaluator", lambda *args, **kw: None)
    monkeypatch.setattr(pipeline.review_selection, "prioritize", lambda selection, *_: selection)

    def refine(*args):
        assert events[-1] == "llm-exit"
        assert len(args[3]) == 2
        events.extend(["large-load", "large-exit"])
        return {"a": captions(), "b": captions()}

    monkeypatch.setattr(transcribe, "refine_clips", refine)
    instance = pipeline.Pipeline(settings, store, run)

    def deliver(clip, *args):
        assert "large-exit" in events
        assert clip["body"]["words"][0]["text"] == "Korjattu"
        events.append("render")
        store.save_clip(clip["id"], run, 1, {**clip["body"], "status": "ready"})

    monkeypatch.setattr(instance, "deliver", deliver)
    selection = dict(review_policy="ranked-v1", verified=[], chat={"status": "unavailable"})
    instance.export_selection(selection, dict(words=[], duration_us=60000000), {}, Path("unused"))
    assert events == ["llm-load", "llm-exit", "large-load", "large-exit", "render", "render"]
    # Caption edits and render retries reuse the saved refinement even if weights are unavailable.
    changed = store.clip("a")["body"]
    changed["words"][0]["text"] = "Ihmisen korjaus"
    changed["status"] = "pending"
    store.save_clip("a", run, 2, changed)
    (model / "manifest.json").unlink()
    instance.refine_captions(Path("unused"), 60000000)
    assert store.clip("a")["body"]["words"][0]["text"] == "Ihmisen korjaus"
    assert len(events) == 6


def test_bad_final_alignment_preserves_original_captions(settings, store, monkeypatch):
    model = prepared(settings, monkeypatch)
    run = store.admit(
        dict(final_transcription=True, model_manifests={"large-v3": digest(model / "manifest.json")}),
        "bad-final",
    )
    for key in ("good", "empty"):
        store.save_clip(key, run, 1, clip_body())
    monkeypatch.setattr(transcribe, "refine_clips", lambda *args: {"good": captions(), "empty": captions([])})
    pipeline.Pipeline(settings, store, run).refine_captions(Path("unused"), 60000000)
    assert store.clip("good")["body"]["status"] == "pending"
    assert store.clip("empty")["body"]["status"] == "pending"
    assert store.clip("empty")["body"]["words"] == clip_body()["words"]
    assert "no words" in store.clip("empty")["body"]["caption_error"]
    assert "Turbo" in store.clip("empty")["body"]["caption_warning"]


def test_editor_uses_final_canonical_words_and_retains_context(settings, store):
    run = store.admit({}, "edit-final")
    store.update(run, state="completed")
    original = clip_body()
    atomic_json(
        settings.work / "runs" / run / "asr/transcript.json",
        {"duration_us": 60000000, "words": original["words"]},
    )
    body = transcribe.apply_refinement(original, captions())
    body["status"] = "ready"
    store.save_clip("clip", run, 1, body)
    layout = store.add_layout({"name": "Test"})
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        visible = client.get("/api/clips/clip").json()
        assert visible["caption_coverage"]["profile"] == "large-v3"
        assert "caption_transcript" not in visible
        edit = dict(
            expected_revision=1,
            start_us=10000000,
            end_us=20000000,
            title="Tarina",
            words=[word(text="Ihmisen korjaus")],
            layout_id=layout,
            reviewed=True,
        )
        assert (
            client.post(
                "/api/clips/clip/edit", headers=headers, json={**edit, "end_us": 31000000}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/clips/clip/edit", headers=headers, json={**edit, "words": [word(start=11500000)]}
            ).status_code
            == 400
        )
        result = client.post("/api/clips/clip/edit", headers=headers, json=edit)
        assert result.status_code == 202, result.text
        saved = store.clip("clip")["body"]
        assert saved["words"][0]["text"] == "Ihmisen korjaus"
        assert saved["caption_transcript"]["words"][0]["text"] == "Korjattu"


def test_audit_preview_keeps_saved_speech_when_final_timing_cannot_align(settings, store, monkeypatch):
    model = prepared(settings, monkeypatch)
    run = store.admit(dict(final_transcription=True,
                          model_manifests={"large-v3": digest(model / "manifest.json")}), "audit-captions")
    original = {**clip_body(), "audit_preview": True}
    store.save_clip("audit", run, 1, original)
    calls = []
    def refine(*args):
        calls.append(True)
        return {"audit": captions([])}
    monkeypatch.setattr(transcribe, "refine_clips", refine)
    for _ in range(2):
        pipeline.Pipeline(settings, store, run).refine_captions(Path("unused"), 60000000)
    body = store.clip("audit")["body"]
    assert body["status"] == "pending" and body["words"] == original["words"]
    assert body["start_us"] == original["start_us"] and body["end_us"] == original["end_us"]
    assert "Turbo" in body["caption_warning"]
    assert calls == [True]


def test_missing_final_speech_preserves_original_for_audit_and_normal_exports(settings, store, monkeypatch):
    model = prepared(settings, monkeypatch)
    run = store.admit(dict(final_transcription=True,
                          model_manifests={"large-v3": digest(model / "manifest.json")}), "missing-captions")
    original = {**clip_body(), "words": [
        word(t * 1000000, t * 1000000 + 700000, id=f"w{t}") for t in range(10, 20)
    ]}
    for key in ("audit", "normal"):
        store.save_clip(key, run, 1, {**original, "audit_preview": key == "audit"})
    partial = captions(original["words"][-1:])
    monkeypatch.setattr(transcribe, "refine_clips", lambda *args: dict(audit=partial, normal=partial))
    pipeline.Pipeline(settings, store, run).refine_captions(Path("unused"), 60000000)
    audit, normal = (store.clip(key)["body"] for key in ("audit", "normal"))
    assert audit["status"] == "pending" and audit["words"] == original["words"]
    assert "Turbo" in audit["caption_warning"]
    assert "omitted speech" in audit["caption_error"]
    assert normal["status"] == "pending" and normal["words"] == original["words"]
    assert "omitted speech" in normal["caption_error"]
