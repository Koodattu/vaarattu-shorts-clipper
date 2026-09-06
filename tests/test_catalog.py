import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import catalog, youtube
from vaarattu_shorts.config import load_settings
from vaarattu_shorts.contracts import CHANNEL_ID
from vaarattu_shorts.pipeline import Pipeline
from vaarattu_shorts.storage import Store
from vaarattu_shorts.web import create_app

NEW = "abc_def-ghI"
OLD = "0123456789_"
OTHER_CHANNEL = "UC" + "a" * 22


def video(vod=NEW, published="2026-09-01T12:00:00Z"):
    return {
        "id": vod,
        "title": "Suomalainen keskustelu",
        "published": published,
        "duration": 3600,
        "available": True,
        "url": f"https://www.youtube.com/watch?v={vod}",
    }


def channel(**extra):
    return {
        "id": CHANNEL_ID,
        "title": "VaarattuVODs",
        "uploads": "UUuploads",
        "next_page_token": "older-page",
        "fetched_at": 1,
        **extra,
    }


def mock_youtube(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setenv("YOUTUBE_API_KEY", "fixture-private-key")
    monkeypatch.setattr(
        catalog.httpx, "Client", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler))
    )


def api_video(vod, live="none"):
    return {
        "id": vod,
        "snippet": {
            "title": "Puhutaan asioista",
            "channelId": CHANNEL_ID,
            "publishedAt": "2026-09-01T12:00:00Z",
            "liveBroadcastContent": live,
        },
        "contentDetails": {"duration": "PT1H2M3S"},
        "status": {"privacyStatus": "public", "uploadStatus": "processed"},
    }


def test_fetch_pages_are_cached_deduplicated_and_key_stays_server_side(settings, store, monkeypatch):
    calls = []

    def handle(req):
        calls.append(req)
        assert req.headers["x-goog-api-key"] == "fixture-private-key"
        assert "fixture-private-key" not in str(req.url)
        resource = req.url.path.rsplit("/", 1)[1]
        if resource == "channels":
            assert req.url.params["id"] == CHANNEL_ID
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": CHANNEL_ID,
                            "snippet": {"title": "VaarattuVODs"},
                            "contentDetails": {"relatedPlaylists": {"uploads": "UUuploads"}},
                        }
                    ]
                },
            )
        if resource == "playlistItems":
            assert req.url.params["maxResults"] == "50"
            older = req.url.params.get("pageToken") == "older-page"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"contentDetails": {"videoId": OLD if older else NEW}},
                        {"contentDetails": {"videoId": OLD if older else NEW}},
                    ],
                    **({} if older else {"nextPageToken": "older-page"}),
                },
            )
        assert resource == "videos"
        vod = req.url.params["id"]
        item = api_video(vod)
        if vod == OLD:
            item["snippet"]["publishedAt"] = "2026-08-01T12:00:00Z"
        return httpx.Response(200, json={"items": [item]})

    mock_youtube(monkeypatch, handle)
    assert catalog.fetch_page(settings, store) == 1
    assert catalog.fetch_page(settings, store, older=True) == 1
    assert len(calls) == 5
    assert store.channel(CHANNEL_ID)["next_page_token"] is None
    assert catalog.fetch_page(settings, store) == 1
    restored = Store(store.path)
    assert [v["id"] for v in restored.videos(CHANNEL_ID)] == [NEW, OLD]
    assert restored.videos(CHANNEL_ID)[0]["duration"] == 3723
    assert restored.channel(CHANNEL_ID)["next_page_token"] == "older-page"
    assert "fixture-private-key" not in json.dumps(restored.channel(CHANNEL_ID))


@pytest.mark.parametrize("failure", ["quota", "timeout", "malformed", "wrong_channel"])
def test_failed_fetch_preserves_page_and_videos(settings, store, monkeypatch, failure):
    store.save_video_page(channel(), [video()])

    def handle(req):
        if failure == "quota":
            return httpx.Response(403, json={"error": {"message": "fixture-private-key"}})
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture-private-key")
        if req.url.path.endswith("playlistItems"):
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": NEW}}]})
        if failure == "malformed":
            return httpx.Response(200, json={"items": [{"id": NEW}]})
        item = api_video(NEW)
        item["snippet"]["channelId"] = OTHER_CHANNEL
        return httpx.Response(200, json={"items": [item]})

    mock_youtube(monkeypatch, handle)
    with pytest.raises(ValueError) as exc:
        catalog.fetch_page(settings, store, older=True)
    assert "fixture-private-key" not in str(exc.value)
    assert store.channel(CHANNEL_ID) == channel()
    assert store.videos(CHANNEL_ID)[0]["title"] == video()["title"]


def test_live_and_missing_videos_are_not_selectable(settings, store, monkeypatch):
    store.save_video_page(channel(), [video(OLD)])

    def handle(req):
        if req.url.path.endswith("playlistItems"):
            return httpx.Response(
                200, json={"items": [{"contentDetails": {"videoId": v}} for v in [NEW, OLD]]}
            )
        return httpx.Response(200, json={"items": [api_video(NEW, live="live")]})

    mock_youtube(monkeypatch, handle)
    catalog.fetch_page(settings, store, older=True)
    assert len(store.videos(CHANNEL_ID)) == 2
    assert all(not v["available"] for v in store.videos(CHANNEL_ID))


def test_processed_history_survives_refresh_restart_retry_and_recent_run_limit(store):
    store.save_video_page(channel(), [video()])
    first = store.admit({"video": NEW}, "first")
    store.update(first, state="completed", result={"coverage": "complete", "outcome": "no_candidates"})
    for i in range(101):
        run = store.admit({"video": OLD}, f"other-{i}")
        store.update(run, state="failed")
    retry = store.admit({"video": NEW}, "retry")
    store.update(retry, state="failed")
    store.save_video_page(channel(), [{**video(), "title": "Updated title"}])
    saved = Store(store.path).videos(CHANNEL_ID)[0]
    assert saved["processed"] and saved["state"] == "failed"
    assert saved["run_id"] == retry and saved["completed_run_id"] == first
    assert saved["title"] == "Updated title"
    assert store.videos(OTHER_CHANNEL) == []


@pytest.mark.parametrize(
    "state,processed",
    [("queued", False), ("paused", False), ("failed", False), ("cancelled", False), ("completed", True)],
)
def test_processing_states_are_not_conflated(store, state, processed):
    store.save_video_page(channel(), [video()])
    run = store.admit({"video": NEW}, "state")
    store.update(run, state=state)
    saved = store.videos(CHANNEL_ID)[0]
    assert saved["state"] == state and saved["processed"] is processed


def test_cached_ui_reads_do_not_fetch_or_start_processing(settings, monkeypatch):
    calls = []
    monkeypatch.setenv("YOUTUBE_API_KEY", "fixture-private-key")

    def fetch(settings, store, older=False):
        calls.append(older)
        store.save_video_page(channel(), [video()])

    monkeypatch.setattr(catalog, "fetch_page", fetch)
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        token = {"X-Local-Token": client.get("/api/status").json()["token"]}
        assert client.get("/api/videos").json()["videos"] == []
        assert calls == []
        assert client.post("/api/videos/refresh").status_code == 403
        response = client.post("/api/videos/refresh", headers=token)
        assert response.json()["videos"][0]["id"] == NEW and calls == [False]
        assert "fixture-private-key" not in response.text
        client.get("/api/videos")
        assert calls == [False]
        assert client.get("/api/runs").json() == []
        assert client.post("/api/videos/unknown", headers=token).status_code == 404


def test_channel_env_and_run_snapshot(settings, store, monkeypatch):
    monkeypatch.setenv("YOUTUBE_CHANNEL_ID", OTHER_CHANNEL)
    assert load_settings(settings.root).youtube_channel_id == OTHER_CHANNEL
    run = store.admit({"video": NEW, "channel_id": CHANNEL_ID}, "channel")
    pipeline = Pipeline(replace(settings, youtube_channel_id=OTHER_CHANNEL), store, run)
    assert pipeline.settings.youtube_channel_id == CHANNEL_ID
    monkeypatch.setenv("YOUTUBE_CHANNEL_ID", "@not-an-id")
    with pytest.raises(ValueError, match="YOUTUBE_CHANNEL_ID"):
        load_settings(settings.root)


def test_ui_run_admission_snapshots_channel(settings, monkeypatch):
    monkeypatch.setattr("vaarattu_shorts.web.preflight", lambda *_: {})
    app = create_app(replace(settings, youtube_channel_id=OTHER_CHANNEL))
    layout = app.state.store.add_layout({"name": "fixture"})
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {
            "X-Local-Token": client.get("/api/status").json()["token"],
            "Idempotency-Key": "catalog-run",
        }
        response = client.post("/api/runs", json={"video": NEW, "layout_id": layout}, headers=headers)
        assert response.status_code == 202
        run = client.get(f"/api/runs/{response.json()['id']}").json()
        assert run["config"]["channel_id"] == OTHER_CHANNEL
        assert run["config"]["asr_batch_size"] == 16
        assert run["config"]["asr_flash_attention"] is False
        assert (
            client.post(
                "/api/runs",
                json={"video": OLD, "layout_id": layout},
                headers={**headers, "Idempotency-Key": "another-run"},
            ).status_code
            == 409
        )


def test_download_metadata_checks_configured_channel(settings, monkeypatch, tmp_path):
    settings = replace(settings, youtube_channel_id=OTHER_CHANNEL)

    def run_tool(*args, **kwargs):
        kwargs["stdout"].write_text(
            json.dumps({"id": NEW, "channel_id": OTHER_CHANNEL, "duration": 60}), encoding="utf-8"
        )

    monkeypatch.setattr(youtube, "run_tool", run_tool)
    assert youtube.metadata(settings, NEW, tmp_path)["channel_id"] == OTHER_CHANNEL
    with pytest.raises(ValueError, match="configured YouTube channel"):
        youtube.metadata(replace(settings, youtube_channel_id=CHANNEL_ID), NEW, tmp_path)


def test_missing_key_and_exhausted_pages_do_not_fetch(settings, store, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="YOUTUBE_API_KEY"):
        catalog.fetch_page(settings, store)
    monkeypatch.setenv("YOUTUBE_API_KEY", "fixture-private-key")
    with pytest.raises(ValueError, match="No older page"):
        catalog.fetch_page(settings, store, older=True)


def test_duration():
    assert catalog.duration_seconds("P1DT2H3M4S") == 93784
    assert catalog.duration_seconds("PT0S") == 0
    assert catalog.duration_seconds("invalid") is None
