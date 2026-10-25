import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import twitch_catalog
from vaarattu_shorts.config import load_settings
from vaarattu_shorts.contracts import CHANNEL_ID
from vaarattu_shorts.storage import Store
from vaarattu_shorts.web import create_app

TITLE = "24.9.2026 - Game night 42 - https://example.com | !koodit"
TWITCH = "https://www.twitch.tv/videos/12345678901"
HTTP_CLIENT = httpx.Client
YT1, YT2 = "abcdefghijk", "lmnopqrstuv"


def yt(identity=YT1, title=TITLE, duration=7200):
    return {
        "id": identity,
        "title": title,
        "duration": duration,
        "published": "2026-10-05T12:00:00Z",
        "url": f"https://www.youtube.com/watch?v={identity}",
        "available": True,
    }


def vod(**kwargs):
    return {
        "id": "12345678901",
        "title": "Game night 42 - https://example.com | !koodit",
        "duration": 7200,
        "published": "2026-09-24T12:00:00Z",
        "url": TWITCH,
        "available": True,
        **kwargs,
    }


def seed(store, youtube=None, twitch=None):
    store.save_video_page({"id": CHANNEL_ID, "title": "Archive", "fetched_at": 1}, youtube or [yt()])
    store.save_twitch_catalog({"id": "vaarattu", "title": "Vaarattu", "fetched_at": 1}, twitch or [vod()])


def library(store):
    return {v["url"]: v for v in store.library_videos(CHANNEL_ID, "vaarattu")}


def finish(store, source, key="run", state="completed", coverage="complete"):
    run = store.admit({"video": source}, key)
    store.update(run, state=state, result={"coverage": coverage, "outcome": "no_candidates"})
    return run


@pytest.mark.parametrize("source", [YT1, TWITCH, f"https://youtu.be/{YT1}"])
def test_same_recording_shares_history_in_both_directions_and_after_restart(store, source):
    seed(store)
    run = finish(store, source)
    rows = library(Store(store.path))
    assert all(v["processed"] and v["match_status"] == "matched" for v in rows.values())
    assert all(v["completed_run_id"] == run for v in rows.values())
    assert sum(v["processed_via_match"] for v in rows.values()) == 1


def parts():
    return [yt(YT2, TITLE + " (Part 2/2)", 2800), yt(YT1, TITLE + " (Part 1/2)", 4400)]


def test_split_export_needs_every_part_and_retains_direct_retry_state(store):
    seed(store, parts())
    first = finish(store, YT1)
    rows = library(store)
    assert rows[TWITCH]["partially_processed"] and not rows[TWITCH]["processed"]
    assert [s["processed"] for s in rows[TWITCH]["matched_sources"]] == [True, False]
    second = finish(store, YT2, "second")
    retry = finish(store, YT1, "retry", state="failed", coverage="partial")
    rows = library(store)
    assert rows[TWITCH]["processed"] and not rows[TWITCH]["partially_processed"]
    assert {s["run_id"] for s in rows[TWITCH]["matched_sources"]} == {first, second}
    assert rows[yt()["url"]]["state"] == "failed"
    assert rows[yt()["url"]]["run_id"] == retry
    assert rows[yt()["url"]]["completed_run_id"] == first


def test_twitch_completion_marks_all_parts_including_parts_imported_later(store):
    seed(store, parts()[:1])
    finish(store, TWITCH)
    assert not library(store)[yt(YT2)["url"]]["processed"]
    store.save_video_page({"id": CHANNEL_ID}, parts()[1:])
    assert all(v["processed"] for v in library(store).values())
    # Expiration disables selection but preserves matching and previous work.
    store.save_twitch_catalog({"id": "vaarattu", "title": "Vaarattu", "fetched_at": 2}, [])
    rows = library(Store(store.path))
    assert not rows[TWITCH]["available"]
    assert all(v["processed"] for v in rows.values())


@pytest.mark.parametrize(
    "state,coverage,expected",
    [
        ("completed", "partial", False),
        ("failed", "partial", False),
        ("queued", None, False),
        ("paused", None, False),
        ("failed", "complete", True),
        ("completed", None, True),
    ],
)
def test_only_completed_analysis_shares_history(store, state, coverage, expected):
    seed(store)
    finish(store, TWITCH, state=state, coverage=coverage)
    assert library(store)[yt()["url"]]["processed"] is expected


@pytest.mark.parametrize(
    "change",
    [
        {"title": "Game night 43 - https://example.com | !koodit"},
        {"published": "2026-09-25T12:00:00Z"},
        {"duration": 7000},
        {"is_live": True},
        {"published": "bad-date"},
    ],
)
def test_unrelated_edited_live_or_invalid_sources_do_not_share_history(store, change):
    seed(store, twitch=[vod(**change)])
    finish(store, TWITCH)
    row = library(store)[yt()["url"]]
    assert not row["processed"] and row["match_status"] == "unmatched"


def test_date_uses_helsinki_and_never_youtube_upload_day(store):
    seed(store, twitch=[vod(published="2026-09-23T22:30:00Z")])
    assert library(store)[TWITCH]["match_status"] == "matched"
    store.save_video_page({"id": CHANNEL_ID}, [yt(title=TITLE.replace("24.9.2026", "5.10.2026"))])
    assert library(store)[TWITCH]["match_status"] == "unmatched"


@pytest.mark.parametrize(
    "youtube,twitch",
    [
        ([yt(), yt(YT2)], [vod()]),
        ([yt()], [vod(), vod(id="999", url="https://www.twitch.tv/videos/999")]),
    ],
)
def test_duplicate_candidates_are_ambiguous_in_both_directions(store, youtube, twitch):
    seed(store, youtube, twitch)
    finish(store, TWITCH)
    rows = library(store)
    assert all(v["match_status"] == "ambiguous" for v in rows.values())
    assert not any(v["processed"] for v in rows.values() if v["provider"] == "youtube")


@pytest.mark.parametrize("youtube", [parts()[:1], [parts()[0], yt(YT1, TITLE + " (Part 2/2)", 4400)]])
def test_missing_or_duplicate_parts_do_not_match(store, youtube):
    seed(store, youtube)
    finish(store, TWITCH)
    rows = library(store)
    assert not any(v["processed"] for v in rows.values() if v["provider"] == "youtube")
    assert rows[youtube[0]["url"]]["match_status"] == "incomplete_parts"


def test_numeric_ids_do_not_collide_across_providers_and_channel_scope(store):
    seed(store, [yt("12345678901", title="Unrelated upload")])
    finish(store, "12345678901")
    assert not library(store)[TWITCH]["processed"]
    assert store.library_videos("other", "other") == []


def test_direct_twitch_processing_remembers_identity_before_expiration(settings, store):
    from datetime import datetime

    metadata = {
        **vod(),
        "provider": "twitch",
        "owner": "vaarattu",
        "timestamp": datetime.fromisoformat(vod()["published"]).timestamp(),
    }
    twitch_catalog.remember_source(settings, store, metadata)
    finish(store, TWITCH)
    store.save_video_page({"id": CHANNEL_ID}, [yt()])
    assert library(store)[yt()["url"]]["processed"]
    store.save_twitch_catalog({"id": "vaarattu"}, [])
    twitch_catalog.remember_source(settings, store, metadata)
    assert not library(store)[TWITCH]["available"], "An old checkpoint must not revive an expired VOD"
    store.save_twitch_catalog({"id": "vaarattu"}, [{**vod(), "is_live": True, "duration": 10}])
    twitch_catalog.remember_source(settings, store, metadata)
    assert library(store)[yt()["url"]]["processed"], "Completed metadata replaces a growing VOD's duration"
    twitch_catalog.remember_source(settings, store, {**metadata, "id": "999", "owner": "someone_else"})
    assert len(library(store)) == 2


@pytest.mark.parametrize("title,duration", [("Game night 42 | !koodit", 7200), (TITLE, 7196)])
def test_missing_recording_date_and_duration_beyond_rounding_do_not_match(store, title, duration):
    seed(store, [yt(title=title, duration=duration)])
    assert library(store)[TWITCH]["match_status"] == "unmatched"


def api_vod(identity="12345678901", **kwargs):
    return {
        "id": identity,
        "user_id": "42",
        "type": "archive",
        "title": vod()["title"],
        "created_at": "2026-09-24T12:00:00Z",
        "duration": "2h0m0s",
        "stream_id": "stream-1",
        "viewable": "public",
        "thumbnail_url": "https://static-cdn.jtvnw.net/thumb-%{width}x%{height}.jpg",
        **kwargs,
    }


def mock_twitch(monkeypatch, pages, *, live=None, failure=None):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "fixture-client")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "fixture-secret")
    calls = []

    def handle(req):
        calls.append(req)
        assert "fixture-secret" not in str(req.url) and "fixture-token" not in str(req.url)
        if req.url.path.endswith("/token"):
            assert b"client_secret=fixture-secret" in req.content
            return httpx.Response(200, json={"access_token": "fixture-token"})
        assert req.headers["Authorization"] == "Bearer fixture-token"
        assert req.headers["Client-ID"] == "fixture-client"
        if req.url.path.endswith("/users"):
            return httpx.Response(
                200, json={"data": [{"id": "42", "login": "vaarattu", "display_name": "Vaarattu"}]}
            )
        if req.url.path.endswith("/streams"):
            return httpx.Response(200, json={"data": live or []})
        assert req.url.params["type"] == "archive" and req.url.params["user_id"] == "42"
        assert req.url.params["sort"] == "time"
        if failure and req.url.params.get("after"):
            return httpx.Response(503, json={"message": "fixture-token fixture-secret"})
        index = int(req.url.params.get("after", "0"))
        return httpx.Response(
            200,
            json={
                "data": pages[index],
                "pagination": {"cursor": str(index + 1)} if index + 1 < len(pages) else {},
            },
        )

    factory = HTTP_CLIENT
    monkeypatch.setattr(
        twitch_catalog.httpx, "Client", lambda **kw: factory(**kw, transport=httpx.MockTransport(handle))
    )
    return calls


def test_twitch_fetch_paginates_preserves_history_and_sanitizes_metadata(settings, store, monkeypatch):
    seed(store)
    finish(store, TWITCH)
    calls = mock_twitch(
        monkeypatch, [[api_vod()], [api_vod("999", stream_id="active")]], live=[{"id": "active"}]
    )
    assert twitch_catalog.fetch(settings, store) == 2
    rows = library(store)
    assert rows[TWITCH]["processed"] and rows[TWITCH]["duration"] == 7200
    assert rows[TWITCH]["thumbnail"].endswith("thumb-320x180.jpg")
    assert not rows["https://www.twitch.tv/videos/999"]["available"]
    assert len(calls) == 5
    assert "fixture-" not in json.dumps(rows)
    mock_twitch(monkeypatch, [[]])
    twitch_catalog.fetch(settings, store)
    assert library(store)[TWITCH]["processed"] and not library(store)[TWITCH]["available"]


@pytest.mark.parametrize("failure", ["later_page", "wrong_owner", "duration", "timestamp", "bad_id"])
def test_failed_twitch_fetch_preserves_entire_catalog(settings, store, monkeypatch, failure):
    seed(store)
    before = library(store)
    bad = {
        "wrong_owner": {"user_id": "other"},
        "duration": {"duration": "broken"},
        "timestamp": {"created_at": "invalid"},
        "bad_id": {"id": "bad"},
    }.get(failure, {})
    mock_twitch(monkeypatch, [[api_vod(**bad)], []], failure=failure == "later_page")
    with pytest.raises(ValueError) as exc:
        twitch_catalog.fetch(settings, store)
    assert "fixture-" not in str(exc.value)
    assert library(store) == before


def test_library_api_twitch_refresh_does_not_require_youtube_key_or_start_runs(settings, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    mock_twitch(monkeypatch, [[api_vod()]])
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        assert client.get("/api/videos").json()["videos"] == []
        assert client.post("/api/videos/twitch").status_code == 403
        response = client.post("/api/videos/twitch", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["videos"][0]["url"] == TWITCH
        assert response.json()["twitch"]["configured"]
        assert not response.json()["configured"]
        assert "fixture-secret" not in response.text and "fixture-token" not in response.text
        assert client.get("/api/runs").json() == []


def test_twitch_configuration(settings, store, monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    assert not twitch_catalog.configured()
    with pytest.raises(ValueError, match="TWITCH_CLIENT"):
        twitch_catalog.fetch(settings, store)
    monkeypatch.setenv("TWITCH_CHANNEL", "Vaarattu")
    assert load_settings(settings.root).twitch_channel == "vaarattu"
    monkeypatch.setenv("TWITCH_CHANNEL", "https://twitch.tv/vaarattu")
    with pytest.raises(ValueError, match="TWITCH_CHANNEL"):
        load_settings(settings.root)
    assert replace(settings, twitch_channel="different").twitch_channel == "different"


@pytest.mark.parametrize(
    "status,body,message",
    [
        (401, "private-secret", "TWITCH_CLIENT"),
        (200, "private-secret", "unreadable"),
        (200, "[]", "unreadable"),
        (429, "private-secret", "preserved"),
    ],
)
def test_twitch_request_errors_are_actionable_and_do_not_echo_response(status, body, message):
    with HTTP_CLIENT(transport=httpx.MockTransport(lambda _: httpx.Response(status, text=body))) as client:
        with pytest.raises(ValueError, match=message) as error:
            twitch_catalog.request(client, "GET", "https://api.twitch.tv/helix/videos")
    assert "private-secret" not in str(error.value)
