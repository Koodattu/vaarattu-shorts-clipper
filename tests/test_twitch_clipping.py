import json

import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import highlight_sources, pipeline, publishing_queue, recordings, stream_data
from vaarattu_shorts.contracts import Candidate, RunRequest, recording_id
from vaarattu_shorts.storage import atomic_json, digest
from vaarattu_shorts.web import create_app

VOD = "https://www.twitch.tv/videos/12345678901"


@pytest.mark.parametrize("value", [VOD, "http://twitch.tv/videos/12345678901?t=1h", "https://m.twitch.tv/videos/12345678901/"])
def test_run_normalizes_twitch_without_confusing_numeric_youtube_ids(value):
    assert RunRequest(video=value, layout_id="a"*32).video == VOD
    assert recording_id("12345678901") == "12345678901"
    assert publishing_queue.source_key(RunRequest(video=value, layout_id="a"*32).video) == VOD
    assert publishing_queue.source_key("https://youtu.be/12345678901") != VOD


@pytest.mark.parametrize("value", ["https://twitch.tv/vaarattu", "https://clips.twitch.tv/example", "https://twitch.tv:8000/videos/123", "https://:secret@twitch.tv/videos/123", "https://twitch.tv.evil.invalid/videos/123", "file:///videos/123"])
def test_clipping_rejects_non_vod_sources(value):
    with pytest.raises(ValueError):
        RunRequest(video=value, layout_id="a"*32)


@pytest.mark.parametrize("live_status", ["was_live", "is_live", "is_upcoming"])
def test_twitch_metadata_only_accepts_completed_recording_and_discards_extractor_data(settings, monkeypatch, live_status):
    folder = settings.work / "metadata"
    def extract(args, settings, folder, name, check, **kwargs):
        assert args[-1] == VOD
        kwargs["stdout"].write_text(json.dumps({"id": "v12345678901", "title": "Game night", "duration": 120,
            "uploader_id": "vaarattu", "upload_date": "20260921", "live_status": live_status,
            "formats": [{"url": "private signed location"}]}), encoding="utf-8")
    monkeypatch.setattr(highlight_sources, "run_tool", extract)
    if live_status != "was_live":
        with pytest.raises(ValueError, match="complete"):
            recordings.metadata(settings, VOD, folder)
    else:
        saved = recordings.metadata(settings, VOD, folder)
        assert saved["provider"] == "twitch" and saved["id"] == "12345678901"
        assert saved["url"] == VOD and "formats" not in saved
    assert not (folder / "metadata.raw.json").exists()


@pytest.mark.parametrize("interval", [None, (10, 30)])
def test_twitch_acquisition_uses_muxed_audio_fallback_and_exact_video_window(settings, monkeypatch, interval):
    calls = []
    def download(args, settings, folder, name, check, **kwargs):
        calls.append(args)
        path = folder / ("source.opus" if interval is None else "source.mkv")
        path.write_bytes(b"media fixture")
        (folder / "download.txt").write_text(str(path), encoding="utf-8")
    monkeypatch.setattr(highlight_sources, "run_tool", download)
    monkeypatch.setattr(highlight_sources.youtube, "probe", lambda *a: {"streams": [{"codec_type": "audio"}]+([] if interval is None else [{"codec_type": "video"}])})
    recordings.acquire(settings, VOD, settings.work / "source", lambda: None, interval)
    args = calls[0]
    assert args[-1] == VOD
    if interval is None:
        assert "bestaudio/best" in args and "-x" in args
    else:
        assert args[args.index("--download-sections")+1] == "*10.000000-30.000000"
        assert "--force-keyframes-at-cuts" in args


def test_twitch_title_matching_never_sends_youtube_association(monkeypatch):
    factory = httpx.Client
    def handle(request):
        assert request.url.params["q"] == "Game night"
        assert "youtubeId" not in request.url.params
        return httpx.Response(200, json={"data": {"matches": [], "suggestedStreamId": None}})
    monkeypatch.setattr(stream_data.httpx, "Client", lambda **kwargs: factory(transport=httpx.MockTransport(handle), **kwargs))
    result = stream_data.enrich({"provider": "twitch", "id": "12345678901", "title": "Game night"}, {})
    assert result["status"] == "timing_unconfirmed" and result["points"] == []


def test_twitch_queue_and_automatic_title_lookup(settings, monkeypatch):
    monkeypatch.setattr("vaarattu_shorts.web.preflight", lambda *a: {})
    app = create_app(settings)
    layout = app.state.store.add_layout({"name": "fixture"})
    seen = []
    def metadata(settings, url, folder):
        assert url == VOD
        return {"title": "Game night"}
    monkeypatch.setattr(recordings, "metadata", metadata)
    monkeypatch.setattr(stream_data, "search", lambda data: seen.append(data) or {"status": "ready", "matches": []})
    with TestClient(app, base_url="http://localhost") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"], "Idempotency-Key": "twitch-clipping"}
        response = client.post("/api/runs", headers=headers, json={"video": VOD+"?t=1h", "layout_id": layout})
        assert response.status_code == 202, response.text
        assert app.state.store.get(response.json()["id"])["config"]["video"] == VOD
        response = client.get("/api/streams/search", params={"video": VOD})
        assert response.status_code == 200 and response.json()["recording_title"] == "Game night"
        assert seen == [{"id": "12345678901", "provider": "twitch", "title": "Game night"}]
        client.get("/api/streams/search", params={"video": VOD, "q": "Manual title"})
        assert seen[-1]["title"] == "Manual title"


def test_twitch_clip_export_render_and_rerender_keep_the_original_provider(settings, store, monkeypatch):
    run = store.admit({"video": VOD, "layout": {}}, "twitch-export")
    process = pipeline.Pipeline(settings, store, run)
    source = process.folder / "source.opus"
    source.write_bytes(b"full audio")
    candidate = Candidate(outcome="accept", start_word_id="w0", end_word_id="w1", idea_word_id="w0",
        category="story", summary_fi="Tarina", title_fi="Tarina", reason="Story", flags=[],
        scores=dict(standalone=4, opening=4, substance=4, payoff=4, fidelity=4))
    selection = {"chat": {"status": "unavailable"}, "review_first": True, "verified": [{"candidate": candidate.model_dump(), "eligible": True,
        "start_us": 10000000, "end_us": 15000000}], "issues": []}
    monkeypatch.setattr(process, "prioritize_selection", lambda s, t: s)
    calls = []
    def acquire(settings, media, folder, check, interval=None):
        assert media["url"] == VOD
        calls.append(interval)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("source.mkv" if interval else "source.opus")
        path.write_bytes(b"fixture")
        return path
    monkeypatch.setattr(highlight_sources, "acquire", acquire)
    monkeypatch.setattr(pipeline.render, "align", lambda *a: {"origin_us": 0, "section_duration": 40})
    def render(settings, section, mapping, body, layout, words, folder, check):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "short.mp4").write_bytes(b"rendered fixture")
        result = {**body, "status": "ready", "video_sha256": digest(folder / "short.mp4")}
        atomic_json(folder / "metadata.json", result)
        return result
    monkeypatch.setattr(pipeline.render, "render_clip", render)
    process.export_selection(selection, {"words": [], "duration_us": 60000000},
        {"id": "12345678901", "title": "Game night", "upload_date": "20260921"}, source)
    clip = store.clips(run)[0]
    assert clip["body"]["status"] == "ready"
    assert clip["body"]["source_url"] == VOD
    assert clip["body"]["source_id"] == "12345678901"
    assert calls == [(0, 25.3)]
    # Re-download original audio and an expanded clip window for a later review edit.
    atomic_json(process.folder / "audio.checkpoint.json", {"result": {"path": str(process.folder / "missing.opus"), "duration": 60}})
    body = {**clip["body"], "status": "pending", "start_us": 40000000, "end_us": 45000000}
    store.save_clip(clip["id"], run, 2, body)
    process.rerender()
    assert calls[-2:] == [None, (30, 55)]
    assert store.clip(clip["id"])["body"]["status"] == "ready"
