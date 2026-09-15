import copy
import json

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import tighten, pacing, pipeline, worker
from vaarattu_shorts.caption_correction import fingerprint
from vaarattu_shorts.contracts import Word
from vaarattu_shorts.web import create_app


@pytest.fixture
def clip(settings, store):
    words = [
        dict(id=f"w{i}", start_us=i * 1000000, end_us=i * 1000000 + 800000, text=f"word{i}")
        for i in range(16)
    ]
    folder = settings.ready / "clip" / "1"
    folder.mkdir(parents=True)
    (folder / "short.mp4").write_bytes(b"preview")
    body = dict(
        words=words,
        start_us=0,
        end_us=16000000,
        status="ready",
        folder=str(folder),
        title="A point",
        tighten_check={"note": "Keep the point"},
    )
    run = store.admit(dict(video="aaaaaaaaaaa", provider="codex", budget_usd=0), "tighten")
    store.save_clip("clip", run, 1, body)
    store.update(run, state="completed", result={"outcome": "completed"})
    return run, body


def cut(a=3, b=6):
    return tighten.Cut(start_word_id=f"w{a}", end_word_id=f"w{b}", reason="Unrelated detour.")


def checked(body, cuts):
    return {
        **body,
        "tighten_check": {
            "id": "check",
            "status": "complete",
            "fingerprint": fingerprint(body),
            "cuts": tighten.resolve(body, cuts),
        },
    }


@pytest.mark.parametrize("a,b", [(0, 3), (3, 15), (5, 3), (4, 4), (2, 99)])
def test_only_internal_passages_can_be_removed(clip, a, b):
    _, body = clip
    with pytest.raises(ValueError):
        tighten.resolve(body, [cut(a, b)])


def test_overlap_uncertain_joins_and_stale_timing_fail_closed(clip):
    _, body = clip
    with pytest.raises(ValueError):
        tighten.resolve(body, [cut(2, 5), cut(4, 7)])
    with pytest.raises(ValueError):
        tighten.resolve(body, [cut(2, 4), cut(5, 7)])
    with pytest.raises(ValueError):
        tighten.resolve(
            {**body, "transcript_timing_issues": [dict(start_us=2800000, end_us=3200000)]}, [cut()]
        )
    nested = copy.deepcopy(body)
    nested["words"][2]["end_us"] = 3500000
    with pytest.raises(ValueError):
        tighten.resolve(nested, [cut()])
    applied = tighten.apply(checked(body, [cut()]), [0])
    applied["words"][3]["start_us"] += 100000
    with pytest.raises(ValueError):
        tighten.intervals(applied)


def test_cut_subsets_preserve_words_and_undo_restores_previous_plan(clip):
    _, body = clip
    proposal = checked(body, [cut(2, 4), cut(9, 11)])
    first = tighten.apply(proposal, [1])
    assert first["words"] == body["words"]
    assert len(first["speech_cuts"]) == 1 and first["speech_cuts"][0]["word_ids"] == ["w9", "w10", "w11"]
    second = tighten.apply(checked(first, [cut(2, 4)]), [0])
    assert len(second["speech_cuts"]) == 2
    assert tighten.undo(second)["speech_cuts"] == first["speech_cuts"]
    assert tighten.undo(first)["speech_cuts"] == []
    for indices in [[], [0, 0], [-1], [2]]:
        with pytest.raises(ValueError):
            tighten.apply(proposal, indices)
    changed = copy.deepcopy(proposal)
    changed["words"][0]["text"] = "edited"
    with pytest.raises(ValueError):
        tighten.apply(changed, [0])


@pytest.mark.parametrize("silence", [True, False])
def test_speech_removal_and_silence_keep_captions_synchronized(clip, silence):
    _, body = clip
    body = copy.deepcopy(body)
    for w in body["words"][8:]:
        w["start_us"] += 3000000
        w["end_us"] += 3000000
    body["end_us"] += 3000000
    body = tighten.apply(checked(body, [cut(3, 8)]), [0])
    words = [Word.model_validate(w) for w in body["words"]]
    plan = pacing.plan(0, body["end_us"], words, enabled=silence, speech_cuts=tighten.intervals(body))
    output = pacing.retime(words, plan)
    assert [w.id for w in output] == [f"w{i}" for i in [0, 1, 2, 9, 10, 11, 12, 13, 14, 15]]
    assert all(a.end_us <= b.start_us for a, b in zip(output, output[1:]))
    assert output[-1].end_us <= plan["output_duration_us"]
    assert plan["output_duration_us"] == sum(
        s["source_end_us"] - s["source_start_us"] for s in plan["retained"]
    )
    assert len(plan["removed"]) == 1, "Overlapping silence and speech removals must not be subtracted twice"
    assert "concat=n=2:v=0:a=1" in pacing.filters(plan, 0)
    assert plan["silence_removed"] if silence else not plan["silence_removed"]


def test_model_gets_actual_speech_and_can_return_no_cuts(clip):
    _, body = clip

    class Evaluator:
        verification_budget = 48000
        verification_reasoning = "low"

        def request_size(self, *_):
            return 100

        def call(self, system, prompt, schema, key, **kwargs):
            data = json.loads(prompt)
            assert data["note"] == "Keep the point" and "title" not in data
            assert "Unclear transcription is NOT evidence of fluff" in system
            result = schema(summary="Keep this clip intact.", cuts=[])
            kwargs["validate"](result)
            return result

    assert tighten.propose(body, None, Evaluator())["cuts"] == []


def test_compact_model_references_resolve_to_saved_word_ids(clip):
    _, body = clip
    body = copy.deepcopy(body)
    for word in body["words"]:
        word["id"] = "original_" + word["id"]

    class Evaluator:
        verification_budget = 48000
        verification_reasoning = "low"

        def request_size(self, *_):
            return 100

        def call(self, system, prompt, schema, key, **kwargs):
            data = json.loads(prompt)
            assert "original_" not in data["speech"]
            assert "[w3] word3" in data["speech"]
            with pytest.raises(ValueError):
                kwargs["validate"](schema(summary="Invalid", cuts=[cut(3, 99)]))
            result = schema(summary="A detour", cuts=[cut()])
            kwargs["validate"](result)
            return result

    result = tighten.propose(body, None, Evaluator())
    assert result["cuts"][0]["word_ids"] == [f"original_w{i}" for i in range(3, 7)]


def test_instructed_edit_uses_distinct_objective_and_current_preview_times(clip):
    _, body = clip
    body = tighten.apply(checked(body, [cut(8, 9)]), [0])
    body["pacing"] = pacing.plan(
        body["start_us"], body["end_us"], [Word.model_validate(w) for w in body["words"]],
        enabled=False, speech_cuts=tighten.intervals(body),
    )
    body["tighten_check"] = {"mode": "instructed", "note": "Remove the middle explanation even if relevant."}

    class Evaluator:
        verification_budget = 48000
        verification_reasoning = "low"

        def request_size(self, system, *args):
            assert system == tighten.INSTRUCTED_SYSTEM
            return 100

        def call(self, system, prompt, schema, key, **kwargs):
            assert system != tighten.SYSTEM and key == "instructed-cuts-v1"
            data = json.loads(prompt)
            assert data["editing_instructions"] == body["tighten_check"]["note"]
            assert "[w10] (8.00s) word10" in data["speech"]
            assert "[w8]" not in data["speech"] and "[w9]" not in data["speech"]
            assert "existing edit join" in data["speech"]
            with pytest.raises(ValueError):
                kwargs["validate"](schema(summary="Unknown speech", cuts=[cut(3, 99)]))
            result = schema(summary="Removed as instructed.", cuts=[cut(3, 6)])
            kwargs["validate"](result)
            return result

    result = tighten.propose(body, None, Evaluator())
    assert result["cuts"][0]["word_ids"] == ["w3", "w4", "w5", "w6"]


def test_short_combined_speech_and_silence_edit_is_rejected_before_rendering(clip):
    _, body = clip
    body = {
        **body,
        "end_us": 12000000,
        "trim_silence": True,
        "words": [
            dict(id=f"w{i}", text="word", start_us=a, end_us=b)
            for i, (a, b) in enumerate(
                [
                    (0, 300000),
                    (2000000, 2300000),
                    (2500000, 5000000),
                    (5200000, 9000000),
                    (9200000, 9500000),
                    (11500000, 12000000),
                ]
            )
        ],
    }
    with pytest.raises(ValueError, match="less than three seconds"):
        tighten.resolve(body, [cut(2, 3)])


def test_edited_speech_timing_survives_final_transcription_and_caption_checks(settings, store, clip):
    from vaarattu_shorts import caption_correction

    run, body = clip
    revised = {**tighten.apply(checked(body, [cut()]), [0]), "status": "pending"}
    store.save_clip("clip", run, 2, revised)
    instance = pipeline.Pipeline(settings, store, run)
    instance.config["final_transcription"] = True
    # No model manifest or audio exists: a speech edit must keep its saved word anchors.
    instance.refine_captions(settings.work / "unused.opus", 16000000)
    assert store.clip("clip")["body"]["words"] == body["words"]
    with pytest.raises(ValueError):
        caption_correction.apply(
            revised, [dict(word_id="w3", original="word3", replacement="different", reason="Correction")]
        )


def test_completed_proposal_resumes_without_another_model_call(settings, store, clip, monkeypatch):
    run, body = clip
    body = checked(body, [cut()])
    store.save_clip("clip", run, 1, body)
    store.update(run, state="running", result={"tighten_requested": "clip", "outcome": "completed"})

    def unexpected(*args, **kwargs):
        pytest.fail("A saved proposal must not be requested again after a restart")

    monkeypatch.setattr(pipeline.Pipeline, "clip_proposal", unexpected)
    worker.run_job(settings, store, run, lambda: False)
    assert store.get(run)["state"] == "completed"
    assert store.get(run)["result"] == {"outcome": "completed"}
    assert store.clip("clip")["body"] == body


@pytest.mark.parametrize("action", ["tighten", "instructed-edit"])
def test_durable_suggestion_application_and_undo(settings, store, clip, monkeypatch, action):
    run, body = clip

    def proposal(self, clip, transcript, folder, **kwargs):
        assert kwargs["propose"] is tighten.propose
        assert (clip["body"]["tighten_check"].get("mode") == "instructed") == (action == "instructed-edit")
        return dict(
            cuts=tighten.resolve(clip["body"], [cut()]),
            summary="Remove detour.",
            fingerprint=fingerprint(clip["body"]),
        )

    monkeypatch.setattr(pipeline.Pipeline, "clip_proposal", proposal)
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": client.get("/api/status").json()["token"]}

    def post(path, data):
        return client.post(f"/api/clips/clip/{path}", headers=headers, json=data)

    if action == "instructed-edit":
        assert post(action, dict(expected_revision=1)).status_code == 422
        assert post(action, dict(expected_revision=1, note="   ")).status_code == 400
    request = post(action, dict(expected_revision=1, note="Keep point"))
    assert request.status_code == 202
    assert post("tighten", dict(expected_revision=1)).status_code == 400
    active = store.admit({}, "background-vod")
    assert store.claim(review=False) == active
    assert store.claim(review=True) == run
    worker.run_job(settings, store, run, lambda: False)
    assert store.get(run)["state"] == "completed" and store.clip("clip")["body"]["words"] == body["words"]
    data = dict(expected_revision=1, check_id=request.json()["request_id"], indices=[0])
    assert post("speech-cuts", {**data, "check_id": "stale"}).status_code == 400
    assert post("speech-cuts", {**data, "expected_revision": 2}).status_code == 400
    assert post("speech-cuts", data).status_code == 202
    revised = store.clip("clip")
    assert revised["revision"] == 2 and revised["body"]["previous_revision"] == 1
    assert revised["body"]["words"] == body["words"] and len(revised["body"]["speech_cuts"]) == 1
    store.update(run, state="completed")
    assert post("context", dict(expected_revision=2)).status_code == 400
    assert post("speech-cuts-undo", dict(expected_revision=2)).status_code == 202
    assert store.clip("clip")["body"]["speech_cuts"] == []
