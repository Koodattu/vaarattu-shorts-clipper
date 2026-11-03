import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import context_repair, pipeline, transcribe, worker
from vaarattu_shorts.contracts import MAX_CLIP_US, Word, clip_duration_limit
from vaarattu_shorts.llm import ModelAnchorError
from vaarattu_shorts.storage import atomic_json
from vaarattu_shorts.web import create_app


@pytest.fixture
def context_clip(settings, store):
    config = dict(video="abc_def-ghI", provider="local", budget_usd=0, model_manifests={})
    run = store.admit(config, "context")
    words = [
        Word(id=f"w{i}", start_us=i * 1000000, end_us=i * 1000000 + 800000, text=f"Sana{i}.").model_dump()
        for i in range(150)
    ]
    folder = settings.ready / "clip" / "1"
    folder.mkdir(parents=True)
    (folder / "short.mp4").write_bytes(b"original preview")
    body = dict(
        title="Tarina",
        start_us=20000000,
        end_us=25000000,
        words=words[20:25],
        folder=str(folder),
        status="ready",
        layout={},
        reviewed=False,
    )
    store.save_clip("clip", run, 1, body)
    atomic_json(folder / "metadata.json", body)
    store.review_clip("clip", 1, "approved", "Original review")
    store.update(run, state="completed", result={"chat": {"status": "unavailable"}})
    run_folder = settings.work / "runs" / run
    transcript = dict(words=words, duration_us=150000000)
    atomic_json(run_folder / "asr" / "transcript.json", transcript)
    source = run_folder / "source.opus"
    source.write_bytes(b"audio")
    atomic_json(run_folder / "audio.checkpoint.json", {"result": {"path": str(source), "duration": 150}})
    return run, body, transcript


def request(before=False, after=False):
    return dict(expected_revision=1, before=before, after=after, note="Include the question")


def proposal(start="w17", end="w24", outcome="expand"):
    return context_repair.ContextProposal(
        outcome=outcome, start_word_id=start, end_word_id=end, reason="Kysymys selittää vastauksen."
    )


@pytest.mark.parametrize(
    "before,after,start,end",
    [
        (False, False, "w17", "w28"),
        (True, False, "w17", "w24"),
        (False, True, "w20", "w28"),
        (True, True, "w17", "w28"),
    ],
)
def test_context_directions_preserve_existing_moment(context_clip, before, after, start, end):
    _, body, transcript = context_clip
    words = [Word.model_validate(w) for w in transcript["words"]]
    a, b = context_repair.bounds(proposal(start, end), body, request(before, after), words)
    assert a <= body["start_us"] and b >= body["end_us"]
    if not after and before:
        assert b == body["end_us"]
    if not before and after:
        assert a == body["start_us"]


@pytest.mark.parametrize(
    "before,after,start,end",
    [
        (True, False, "w20", "w28"),
        (False, True, "w17", "w24"),
        (False, False, "w20", "w24"),
        (True, True, "invented", "w24"),
        (True, True, "w50", "w60"),
        (True, True, "w24", "w17"),
    ],
)
def test_context_rejects_wrong_side_no_added_speech_and_invalid_ids(
    context_clip, before, after, start, end
):
    _, body, transcript = context_clip
    with pytest.raises(ModelAnchorError):
        context_repair.bounds(
            proposal(start, end),
            body,
            request(before, after),
            [Word.model_validate(w) for w in transcript["words"]],
        )


def test_long_context_proposal_survives_final_transcription(context_clip):
    _, body, transcript = context_clip
    body = {**body, "context_request": request()}

    class Evaluator:
        verification_reasoning = "low"
        verification_budget = 48000

        def request_size(self, *_):
            return 100

        def call(self, system, prompt, schema, key, *, validate, reasoning_effort):
            assert "<=90" not in system
            assert "[w110]" in json.loads(prompt)["surrounding_source_speech"]
            result = proposal("w1", "w110")
            validate(result)
            return result

    _, revised = context_repair.propose(body, transcript, Evaluator())
    assert revised["end_us"] - revised["start_us"] == 109800000
    assert revised["context_expanded"] is True
    final = {**transcript, "start_us": 0, "end_us": transcript["duration_us"], "timing_issues": []}
    refined = transcribe.apply_refinement(revised, final)
    assert refined["end_us"] - refined["start_us"] > MAX_CLIP_US
    assert clip_duration_limit(refined) > 600000000
    assert clip_duration_limit(body) == MAX_CLIP_US


@pytest.mark.parametrize("expanded", [False, True])
def test_editor_allows_long_context_revisions_only(settings, store, context_clip, expanded):
    run, original, transcript = context_clip
    body = {**original, "context_expanded": expanded, "start_us": 1000000,
            "end_us": 110800000, "words": transcript["words"][1:111]}
    store.save_clip("clip", run, 1, body)
    layout = store.add_layout({"name": "Test"})
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        assert client.get("/api/clips/clip").json()["context_expanded"] is expanded
        result = client.post("/api/clips/clip/edit", headers=headers, json=dict(
            expected_revision=1, start_us=1000000, end_us=110800000, title="Updated title",
            words=body["words"], layout_id=layout, reviewed=True,
        ))
        assert result.status_code == (202 if expanded else 400), result.text
    if expanded:
        assert store.clip("clip")["revision"] == 2
        assert store.clip("clip")["body"]["context_expanded"] is True


def test_proposal_uses_current_caption_text_and_original_surrounding_ids(context_clip):
    _, body, transcript = context_clip
    body = {**body, "context_request": request(True), "caption_transcript": {"profile": "large-v3"},
            "caption_warning": "Old fallback", "caption_error": "Old failure", "audit_caption_warning": "Old audit"}
    body["words"][0] = {**body["words"][0], "text": "Human correction"}

    class Evaluator:
        verification_reasoning = "medium"
        verification_budget = 48000

        def request_size(self, *_):
            return 100

        def call(self, system, prompt, schema, key, *, validate, reasoning_effort):
            assert reasoning_effort == "medium"
            supplied = json.loads(prompt)
            assert "Human correction" in supplied["current_clip_text"]
            assert "[w17] Sana17." in supplied["surrounding_source_speech"]
            assert supplied["human_note"] == "Include the question"
            result = proposal()
            validate(result)
            return result

    result, revised = context_repair.propose(body, transcript, Evaluator())
    assert result.outcome == "expand"
    assert revised["start_us"] == 17000000 and revised["end_us"] == 25000000
    assert revised["words"][3]["text"] == "Human correction"
    assert "caption_transcript" not in revised
    assert "caption_warning" not in revised and "caption_error" not in revised
    assert "audit_caption_warning" not in revised
    assert revised["status"] == "pending" and revised["folder"] is None


def test_context_endpoint_queues_without_changing_preview_or_review(settings, store, context_clip):
    run, original, _ = context_clip
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        endpoint = "/api/clips/clip/context"
        assert client.post(endpoint, json=request()).status_code == 403
        assert (
            client.post(endpoint, headers=headers, json={**request(), "note": "x" * 2001}).status_code == 422
        )
        assert (
            client.post(endpoint, headers=headers, json={**request(), "expected_revision": 2}).status_code
            == 400
        )
        result = client.post(endpoint, headers=headers, json=request(True))
        assert result.status_code == 202, result.text
        queued = store.clip("clip")
        assert queued["revision"] == 1 and queued["review_status"] == "approved"
        assert all(queued["body"][key] == value for key, value in original.items())
        assert store.get(run)["result"]["context_repair_requested"] == "clip"
        assert store.get(run)["state"] == "queued"
        assert client.get("/api/artifacts/clip/video").content == b"original preview"
        assert client.post(endpoint, headers=headers, json=request()).status_code == 400
        assert (
            client.post(
                "/api/clips/clip/review",
                headers=headers,
                json={"expected_revision": 1, "status": "not_approved"},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/clips/clip/rerender", headers=headers, json={"expected_revision": 1}
            ).status_code
            == 400
        )


@pytest.mark.parametrize("outcome", ["expand", "unchanged"])
def test_repair_context_end_to_end_and_resume_after_model_result(
    settings, store, monkeypatch, context_clip, outcome
):
    run, original, transcript = context_clip
    queued = store.queue_context("clip", request(True))
    store.claim()
    events = []

    @contextmanager
    def server(*args):
        events.append("llm-load")
        try:
            yield None
        finally:
            events.append("llm-exit")

    class Evaluator:
        verification_budget = 48000
        verification_reasoning = "low"

        def __init__(self, *args, **kwargs):
            pass

        def request_size(self, *_):
            return 100

        def call(self, *args, validate, **kw):
            result = proposal(outcome=outcome)
            validate(result)
            return result

    monkeypatch.setattr(pipeline, "local_server", server)
    monkeypatch.setattr(pipeline, "Evaluator", Evaluator)
    instance = pipeline.Pipeline(settings, store, run)

    def deliver(clip, *args):
        assert events[-1] == "llm-exit"
        events.append("render")
        assert clip["revision"] == 2 and clip["body"]["previous_revision"] == 1
        assert clip["review_status"] == "approved"
        store.save_clip("clip", run, 2, {**clip["body"], "status": "ready"})

    monkeypatch.setattr(instance, "deliver", deliver)
    instance.repair_context()
    saved = store.clip("clip")
    assert store.get(run)["state"] == "completed"
    assert "context_repair_requested" not in store.get(run)["result"]
    assert (instance.folder / "context-repair" / queued["request_id"] / "proposal.json").is_file()
    assert (settings.ready / "clip/1/short.mp4").read_bytes() == b"original preview"
    assert json.loads((instance.folder / "asr/transcript.json").read_text()) == transcript
    if outcome == "unchanged":
        assert events == ["llm-load", "llm-exit"]
        assert saved["revision"] == 1 and saved["review_status"] == "approved"
        assert all(saved["body"][key] == value for key, value in original.items())
    else:
        assert events == ["llm-load", "llm-exit", "render"]
        assert saved["revision"] == 2
        # A pause after the result was applied must not spend on a second model call.
        store.update(
            run, state="running", result={**store.get(run)["result"], "context_repair_requested": "clip"}
        )
        instance.repair_context()
        assert events == ["llm-load", "llm-exit", "render"]


@pytest.mark.parametrize(
    "flag,stage,expected",
    [
        ("context_repair_requested", "final-transcript", "context"),
        ("context_repair_requested", "render", "context"),
        ("rerender_requested", "final-transcript", "render"),
        ("rerender_requested", "render", "render"),
    ],
)
def test_worker_resumes_requested_action_across_stage_changes(
    settings, store, monkeypatch, flag, stage, expected
):
    run = store.admit({}, "dispatch")
    store.update(run, stage=stage, result={flag: True})
    calls = []

    class Pipeline:
        def __init__(self, *args):
            pass

        def repair_context(self):
            calls.append("context")

        def rerender(self):
            calls.append("render")

        def execute(self):
            pytest.fail("A clip revision must not restart discovery")

    monkeypatch.setattr(worker, "Pipeline", Pipeline)
    worker.run_job(settings, store, run, lambda: False)
    assert calls == [expected]


@pytest.mark.parametrize("state", ["failed", "cancelled", "paused", "completed"])
def test_context_recovery_keeps_original_clip_and_unlocks_editing(settings, store, context_clip, state):
    run, original, _ = context_clip
    queued = store.queue_context("clip", request(True))
    store.update(run, state=state, message="Model service failed")
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": client.get("/api/status").json()["token"]}
    clip = client.get("/api/clips/clip").json()
    assert clip["context_job"]["state"] == state and clip["context_recovery_available"]
    response = client.post("/api/clips/clip/context-recovery", headers=headers, json={
        "expected_revision": 1, "request_id": queued["request_id"], "action": "discard"})
    assert response.status_code == 200, response.text
    assert response.json()["context_request"]["status"] == "cancelled"
    assert response.json()["context_job"] is None
    saved = store.clip("clip")
    assert saved["revision"] == 1 and saved["review_status"] == "approved"
    assert all(saved["body"][key] == value for key, value in original.items())
    assert (settings.ready / "clip/1/short.mp4").read_bytes() == b"original preview"
    assert store.get(run)["state"] == "completed"
    assert "context_repair_requested" not in store.get(run)["result"]
    store.queue_context("clip", request())


def test_running_context_must_stop_before_discard_or_retry(settings, store, context_clip):
    run, original, _ = context_clip
    queued = store.queue_context("clip", request())
    assert store.claim() == run
    store.recover_context("clip", 1, queued["request_id"], "stop")
    assert store.get(run)["state"] == "running" and store.get(run)["intent"] == "cancel"
    for action in ("discard", "retry"):
        with pytest.raises(ValueError, match="wait"):
            store.recover_context("clip", 1, queued["request_id"], action)
    worker.run_job(settings, store, run, lambda: False)
    assert store.get(run)["state"] == "cancelled"
    store.recover_context("clip", 1, queued["request_id"], "discard")
    assert store.get(run)["state"] == "completed"
    assert store.clip("clip")["body"]["words"] == original["words"]


def test_queued_context_can_stop_and_retry_with_new_model_and_stale_results_are_rejected(settings, store, context_clip, monkeypatch):
    run, _, _ = context_clip
    queued = store.queue_context("clip", request(True))
    store.recover_context("clip", 1, queued["request_id"], "stop")
    assert store.claim() is None
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": client.get("/api/status").json()["token"]}
    response = client.post("/api/clips/clip/context-recovery", headers=headers, json={
        "expected_revision": 1, "request_id": queued["request_id"], "action": "retry",
        "analysis": {"provider": "openai", "budget_usd": 2}})
    assert response.status_code == 200, response.text
    retry = response.json()["context_request"]
    assert retry["id"] != queued["request_id"] and "started_at" not in retry
    assert retry["before"] is True and retry["note"] == "Include the question"
    assert retry["analysis"] == {"provider": "openai", "budget_usd": 2}
    assert store.get(run)["config"]["provider"] == "local"
    with pytest.raises(ValueError, match="changed"):
        store.resolve_context("clip", 1, queued["request_id"], "Late response", {})
    with pytest.raises(ValueError, match="changed"):
        store.recover_context("clip", 1, queued["request_id"], "stop")
    assert store.get(run)["state"] == "queued"


def test_context_deadline_includes_retries_and_survives_resume(settings, store, context_clip, monkeypatch):
    run, original, _ = context_clip
    queued = store.queue_context("clip", request())
    store.claim()
    clock = [1000.0]
    monkeypatch.setattr(pipeline.time, "time", lambda: clock[0])

    @contextmanager
    def server(*args):
        yield None

    class Evaluator:
        def __init__(self, *args, **kwargs):
            self.check = args[5]

    def delayed_proposal(body, transcript, evaluator):
        clock[0] += context_repair.CONTEXT_TIMEOUT_SECONDS
        evaluator.check()
        pytest.fail("An expired search must not apply a late result")

    monkeypatch.setattr(pipeline, "local_server", server)
    monkeypatch.setattr(pipeline, "Evaluator", Evaluator)
    monkeypatch.setattr(context_repair, "propose", delayed_proposal)
    worker.run_job(settings, store, run, lambda: False)
    assert store.get(run)["state"] == "failed"
    assert "timed out after 15 minutes" in store.get(run)["message"]
    assert store.clip("clip")["body"]["words"] == original["words"]
    assert store.start_context("clip", 1, queued["request_id"]) == 1000
    store.recover_context("clip", 1, queued["request_id"], "retry")
    retry = store.clip("clip")["body"]["context_request"]
    assert store.start_context("clip", 1, retry["id"]) == clock[0]


def test_context_recovery_cannot_undo_an_applied_revision(store, context_clip):
    _, original, _ = context_clip
    queued = store.queue_context("clip", request())
    store.resolve_context("clip", 1, queued["request_id"], "Expanded", {**original, "status": "pending"})
    with pytest.raises(ValueError, match="changed"):
        store.recover_context("clip", 1, queued["request_id"], "discard")
    assert store.clip("clip")["revision"] == 2


def test_local_token_counting_honors_context_cancellation(tmp_path):
    import httpx
    from vaarattu_shorts.llm import Evaluator
    from vaarattu_shorts.processes import Interrupted

    class Client:
        def post(self, url, **kwargs):
            body = {"prompt": "test"} if url == "/apply-template" else {"tokens": [1, 2]}
            return httpx.Response(200, json=body, request=httpx.Request("POST", "http://localhost" + url))

    def cancelled():
        raise Interrupted("cancel")

    evaluator = Evaluator("local", None, "run", tmp_path, 0, cancelled, Client())
    with pytest.raises(Interrupted):
        evaluator.input_size("system", "prompt")
