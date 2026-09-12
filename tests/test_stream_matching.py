import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import stream_data
from vaarattu_shorts.contracts import RunRequest
from vaarattu_shorts.web import create_app


def mock_client(monkeypatch, handle):
    factory = httpx.Client
    monkeypatch.setattr(stream_data.httpx, "Client", lambda **kwargs: factory(transport=httpx.MockTransport(handle), **kwargs))


def test_search_uses_exact_id_and_never_enables_chat_from_a_title(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        assert request.url.params["youtubeId"] == "aaaaaaaaaaa"
        return httpx.Response(200, json={"data": {"matches": [{"id": 9, "streamOffsetSeconds": 12000, "identity": "linked"}], "suggestedStreamId": 9}})

    mock_client(monkeypatch, handle)
    result = stream_data.enrich({"id": "aaaaaaaaaaa", "title": "Archive part 2"}, {})
    assert result["suggestedStreamId"] == 9
    assert result["status"] == "timing_unconfirmed" and result["points"] == []
    assert calls == ["/api/streams/search"]


def test_manual_stream_bypasses_search_even_for_old_streams_and_maps_split_offset(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/activity"):
            return httpx.Response(200, json={"data": {"intervalMinutes": 1, "points": [{"time": "2026-07-09T15:21:00Z", "endTime": "2026-07-09T15:22:00Z", "activeChatters": 20}]}})
        assert request.url.path == "/api/streams/1"
        return httpx.Response(200, json={"data": {"id": 1, "startTime": "2026-07-09T12:00:00Z"}})

    mock_client(monkeypatch, handle)
    result = stream_data.enrich({"title": "unrelated"}, {"stream_id": 1, "stream_offset_seconds": 12000, "alignment_confirmed": True})
    assert result["status"] == "aligned"
    assert result["points"][0]["start_us"] == 60000000
    assert len(calls) == 2


@pytest.mark.parametrize("duplicate", [False, True])
def test_older_backend_falls_back_without_confirming_ambiguous_identity(monkeypatch, duplicate):
    def handle(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(404)
        stream = {"id": 1, "startTime": "2026-07-08T22:00:00Z", "segments": [{"title": "hyvää päivää -> https://example.com"}]}
        return httpx.Response(200, json={"data": [stream, {**stream, "id": 2}] if duplicate else [stream]})

    mock_client(monkeypatch, handle)
    result = stream_data.search({"id": "aaaaaaaaaaa", "title": "9.7.2026 - HYVÄÄ päivää!"})
    assert result["suggestedStreamId"] == (None if duplicate else 1)
    assert result["matches"][0]["alignment"] == "unknown"
    assert result["source"] == "legacy"


def test_api_outage_preserves_transcript_only_path(monkeypatch):
    mock_client(monkeypatch, lambda _: httpx.Response(503))
    result = stream_data.enrich({"id": "aaaaaaaaaaa", "title": "title"}, {})
    assert result["status"] == "unavailable" and result["points"] == []


def test_local_lookup_validates_inputs_and_returns_suggestions(settings, monkeypatch):
    seen = []
    monkeypatch.setattr(stream_data, "search", lambda metadata: seen.append(metadata) or {"matches": [], "status": "ready"})
    monkeypatch.setattr(stream_data, "stream_detail", lambda stream_id: {"id": stream_id})
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    assert client.get("/api/streams/search", params={"video": "https://youtu.be/aaaaaaaaaaa", "q": "A title"}).status_code == 200
    assert seen == [{"id": "aaaaaaaaaaa", "title": "A title"}]
    assert client.get("/api/streams/search", params={"video": "file:///private"}).status_code == 400
    assert client.get("/api/streams/search", params={"video": "aaaaaaaaaaa", "q": "x" * 301}).status_code == 400
    assert client.get("/api/streams/0").status_code == 400
    assert client.get("/api/streams/2147483648").status_code == 400
    assert client.get("/api/streams/254").json() == {"id": 254}
    with pytest.raises(ValueError):
        RunRequest(video="aaaaaaaaaaa", layout_id="a" * 32, stream_id=2147483648)
