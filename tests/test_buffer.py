import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import buffer, delivery, r2
from vaarattu_shorts.storage import Store
from vaarattu_shorts.web import create_app
from test_posting_and_cleanup import rendered


@pytest.fixture
def remote(settings, store, monkeypatch):
    for key in ("BUFFER_API_KEY", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(key, raising=False)
    state = {"posts": {}, "objects": {}, "mutations": [], "fail_channel": None, "fail_type": "timeout",
             "requests": [], "sleeps": []}
    channels = [dict(id=p, name=p, displayName=p, service=p, organizationId="org", isDisconnected=False,
                     isLocked=False, isQueuePaused=False) for p in buffer.PLATFORMS]
    state["channels"] = channels
    original = httpx.Client

    def respond(request):
        state["requests"].append(request)
        if request.url.host == "publishing.vaarattu.tv":
            data = state["objects"].get(request.url.path, b"")
            return httpx.Response(200 if data else 404, headers={"content-type": "video/mp4", "content-length": str(len(data))})
        if request.url.host == f"{r2.ACCOUNT}.r2.cloudflarestorage.com":
            key = request.url.path.removeprefix("/" + r2.BUCKET)
            if request.method == "PUT":
                state["objects"][key] = request.content
            elif request.method == "DELETE":
                del state["objects"][key]
            return httpx.Response(200, text="<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>")
        assert request.url.host == "api.buffer.com"
        assert request.headers["authorization"] == "Bearer " + "test-key-" * 4
        body = json.loads(request.content)
        query, variables = body["query"], body["variables"]
        if query.startswith("query Organizations"):
            data = {"account": {"organizations": [{"id": "org", "name": "Test"}]}}
        elif query.startswith("query Channels"):
            assert variables["input"] == {"organizationId": "org"}
            data = {"channels": channels}
        elif query.startswith("query Posts"):
            filters = variables["input"]["filter"]
            posts = [p for p in state["posts"].values() if p["channelId"] in filters["channelIds"] and
                     ("status" not in filters or p["status"] in filters["status"])]
            data = {"posts": {"edges": [{"node": p} for p in posts], "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        elif query.startswith("query Post("):
            data = {"post": state["posts"][variables["input"]["id"]]}
        else:
            assert query.startswith("mutation CreatePost")
            payload = variables["input"]
            assert any(j["receipts"].get(payload["channelId"], {}).get("status") == "submitting"
                       for j in buffer.publications(store).values()), "Receipt must be durable before sending"
            state["mutations"].append(payload)
            failed = payload["channelId"] == state["fail_channel"]
            if failed and state["fail_type"] == "invalid":
                return httpx.Response(200, json={"data": {"createPost": {"__typename": "InvalidInputError", "message": "Invalid caption"}}})
            post = dict(id=str(len(state["posts"]) + 1), channelId=payload["channelId"], status="scheduled",
                        text=payload["text"], dueAt=payload.get("dueAt"), schedulingType="automatic", externalLink=None,
                        assets=[{"source": payload["assets"][0]["video"]["url"]}], error=None)
            state["posts"][post["id"]] = post
            if failed:
                raise httpx.ReadTimeout("lost response", request=request)
            data = {"createPost": {"__typename": "PostActionSuccess", "post": post}}
        return httpx.Response(200, json={"data": data})

    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(buffer.time, "sleep", state["sleeps"].append)
    (settings.root / ".env").write_text("OTHER_SETTING=keep\n")
    buffer.connect(settings, "test-key-" * 4)
    r2.connect(settings, "a" * 32, "b" * 64)
    run = store.admit({"video": "source"}, "buffer")
    store.update(run, state="completed")
    rendered(settings, store, run, "early", 10_000_000)
    rendered(settings, store, run, "late", 30_000_000)
    state["run"] = run
    return state


def item(clip="early", **changes):
    return {"clip_id": clip, "revision": 1, "title": "Finnish clip", "caption": "Suomeksi! #gaming", **changes}


def test_instant_publishing_through_http_api_uploads_once_and_records_all_platforms(settings, store, remote):
    api = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": api.get("/api/status").json()["token"]}
    preview = api.post("/api/publishing/buffer/preview", headers=headers, json={"items": [item()], "mode": "now"})
    assert preview.status_code == 200, preview.text
    assert not remote["mutations"] and not remote["objects"], "Preview must not upload or publish"
    url = f"/api/publishing/buffer/send/{preview.json()['id']}/early"
    sent = api.post(url, headers=headers)
    assert sent.status_code == 200, sent.text
    assert set(sent.json()["receipts"]) == set(buffer.PLATFORMS)
    assert len(remote["mutations"]) == 3
    assert list(remote["objects"].values()) == [b"final-early"]
    for payload in remote["mutations"]:
        assert payload["mode"] == "shareNow" and "dueAt" not in payload
        assert payload["schedulingType"] == "automatic"
        assert payload["text"] == "Suomeksi! #gaming"
    assert remote["mutations"][0]["metadata"]["youtube"] == dict(title="Finnish clip", categoryId="20", privacy="public", madeForKids=False)
    assert remote["mutations"][1]["metadata"]["instagram"] == dict(type="reel", shouldShareToFeed=True)
    assert "metadata" not in remote["mutations"][2]
    assert api.post(url, headers=headers).status_code == 200
    assert len(remote["mutations"]) == 3
    assert api.post(url).status_code == 403
    local = api.get("/api/publishing/buffer").json()
    assert "test-key-" not in json.dumps(local)
    assert "OTHER_SETTING=keep" in (settings.root / ".env").read_text()


def test_daily_plan_preserves_order_and_locks_manual_receipts(settings, store, remote):
    delivery.schedule(store, [remote["run"]], "2030-10-25", "18:00", "Europe/Helsinki")
    ticket = buffer.preview(settings, store, [item("late"), item()], "plan")
    assert [i["clip_id"] for i in ticket["items"]] == ["early", "late"]
    with pytest.raises(ValueError, match="earlier clip"):
        buffer.send(settings, store, ticket["id"], "late")
    for clip in ("early", "late"):
        buffer.send(settings, store, ticket["id"], clip)
    assert all(m["mode"] == "customScheduled" for m in remote["mutations"])
    assert remote["mutations"][0]["dueAt"] == "2030-10-25T15:00:00+00:00"
    plan = delivery.posting_plan(store)
    assert plan[0]["buffer_managed"]
    assert all(r["status"] == "scheduled" for r in plan[0]["deliveries"].values())
    with pytest.raises(ValueError, match="Buffer"):
        delivery.unschedule(store, plan[0]["id"])
    with pytest.raises(ValueError, match="Buffer"):
        delivery.record_post(store, plan[0]["id"], "youtube", "https://youtube.com/shorts/test")


def test_uncertain_creation_survives_restart_and_reconciles_without_duplicate(settings, store, remote):
    remote["fail_channel"] = "instagram"
    ticket = buffer.preview(settings, store, [item()], "now")
    job = buffer.send(settings, store, ticket["id"], "early")
    assert [r["status"] for r in job["receipts"].values()] == ["scheduled", "unknown", "pending"]
    assert len(remote["mutations"]) == 2 and not remote["sleeps"]
    restarted = Store(settings.work / "state.sqlite3")
    with pytest.raises(ValueError, match="uncertain"):
        buffer.send(settings, restarted, ticket["id"], "early")
    buffer.refresh(settings, restarted)
    assert buffer.publications(restarted)["early"]["receipts"]["instagram"]["status"] == "scheduled"
    remote["fail_channel"] = None
    resume = buffer.preview(settings, restarted, [item()], "resume")
    buffer.send(settings, restarted, resume["id"], "early")
    assert [m["channelId"] for m in remote["mutations"]] == ["youtube", "instagram", "tiktok"]
    assert len(remote["objects"]) == 1


def test_ambiguous_matches_require_explicit_resolution(settings, store, remote):
    remote["fail_channel"] = "youtube"
    ticket = buffer.preview(settings, store, [item()], "now")
    buffer.send(settings, store, ticket["id"], "early")
    remote["posts"]["duplicate"] = {**remote["posts"]["1"], "id": "duplicate"}
    buffer.refresh(settings, store)
    assert buffer.publications(store)["early"]["receipts"]["youtube"]["status"] == "unknown"
    with pytest.raises(ValueError, match="confirm"):
        buffer.resolve(settings, store, "early", "youtube")
    with pytest.raises(ValueError, match="Resolve"):
        buffer.preview(settings, store, [item()], "resume")
    buffer.resolve(settings, store, "early", "youtube", post_id="1")
    assert buffer.publications(store)["early"]["receipts"]["youtube"]["post_id"] == "1"


def test_known_rejection_retries_only_missing_platforms(settings, store, remote):
    remote.update(fail_channel="instagram", fail_type="invalid")
    ticket = buffer.preview(settings, store, [item()], "now")
    job = buffer.send(settings, store, ticket["id"], "early")
    assert job["receipts"]["instagram"]["status"] == "failed"
    remote["fail_channel"] = None
    resume = buffer.preview(settings, store, [item()], "resume")
    buffer.send(settings, store, resume["id"], "early")
    assert [m["channelId"] for m in remote["mutations"]].count("youtube") == 1
    assert len(remote["posts"]) == 3


@pytest.mark.parametrize("change", ["review", "file", "channels", "plan"])
def test_changed_preview_cannot_publish(settings, store, remote, change):
    delivery.schedule(store, [remote["run"]], "2030-01-01", "18:00", "Europe/Helsinki")
    ticket = buffer.preview(settings, store, [item()], "plan")
    if change == "review":
        store.review_clip("early", 1, "approved")
    elif change == "file":
        (settings.ready / "early" / "1" / "short.mp4").write_bytes(b"changed")
    elif change == "channels":
        buffer.configure(settings, "org", {"youtube": "youtube"})
    else:
        delivery.unschedule(store, delivery.posting_plan(store)[0]["id"])
    with pytest.raises(ValueError, match="changed"):
        buffer.send(settings, store, ticket["id"], "early")
    assert not remote["mutations"] and not remote["objects"]


def test_queue_capacity_is_checked_again_after_preview(settings, store, remote):
    ticket = buffer.preview(settings, store, [item()], "now")
    for i in range(10):
        remote["posts"][str(i)] = {"id": str(i), "channelId": "youtube", "status": "scheduled"}
    with pytest.raises(ValueError, match="queue filled"):
        buffer.send(settings, store, ticket["id"], "early")
    with pytest.raises(ValueError, match="no free queue slots"):
        buffer.preview(settings, store, [item()], "now")
    assert not remote["objects"]


def test_paused_channel_and_expired_preview_block_submission(settings, store, remote):
    remote["channels"][0]["isQueuePaused"] = True
    with pytest.raises(ValueError, match="Resume"):
        buffer.preview(settings, store, [item()], "now")
    remote["channels"][0]["isQueuePaused"] = False
    ticket = buffer.preview(settings, store, [item()], "now")
    with store.connect() as db:
        db.execute("UPDATE buffer_previews SET expires=0")
    with pytest.raises(ValueError, match="expired"):
        buffer.send(settings, store, ticket["id"], "early")
    assert not remote["mutations"]


def test_uncertain_absent_confirmation_resets_plan_and_allows_discard(settings, store, remote):
    delivery.schedule(store, [remote["run"]], "2030-01-01", "18:00", "Europe/Helsinki")
    remote["fail_channel"] = "youtube"
    ticket = buffer.preview(settings, store, [item()], "plan")
    buffer.send(settings, store, ticket["id"], "early")
    with pytest.raises(ValueError, match="may have created"):
        buffer.discard_unsubmitted(store, "early")
    remote["posts"].clear()
    buffer.resolve(settings, store, "early", "youtube", absent_confirmed=True)
    assert delivery.posting_plan(store)[0]["deliveries"]["youtube"]["status"] == "pending"
    buffer.discard_unsubmitted(store, "early")
    assert "early" not in buffer.publications(store)
    assert not delivery.posting_plan(store)[0]["buffer_managed"]


def test_cleanup_preserves_submitted_revision_after_local_edit(settings, store, remote):
    ticket = buffer.preview(settings, store, [item()], "now")
    buffer.send(settings, store, ticket["id"], "early")
    first = store.clip("early")["body"]
    folder = settings.ready / "early" / "2"
    folder.mkdir()
    (folder / "short.mp4").write_bytes(b"new-final")
    store.save_clip("early", remote["run"], 2, {**first, "folder": str(folder), "video_sha256": r2.digest(folder / "short.mp4")})
    store.review_clip("early", 2, "ready_to_post")
    with store.connect() as db:
        preview = delivery.cleanup_plan(settings, store, db, remote["run"])
    assert preview["kept_videos"] == 3
    assert not any(f[0].replace("\\", "/") == "ready/early/1/short.mp4" for f in preview["files"])


def test_parallel_submission_endpoint_is_locked(settings, remote):
    from vaarattu_shorts.processes import lock

    api = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": api.get("/api/status").json()["token"]}
    with lock(settings.work / "buffer-publishing.lock"):
        response = api.post("/api/publishing/buffer/preview", headers=headers, json={"items": [item()], "mode": "now"})
    assert response.status_code == 400
    assert "in progress" in response.text
    assert not remote["mutations"]


def test_custom_time_dst_and_future_validation(settings, store, remote):
    ticket = buffer.preview(settings, store, [item()], "schedule", "Europe/Helsinki", "2030-01-01T18:00")
    assert ticket["items"][0]["due_at"] == "2030-01-01T16:00:00+00:00"
    for value in ("2030-03-31T03:30", "2030-10-27T03:30", "2030-01-01T18:00+02:00"):
        with pytest.raises(ValueError, match="valid local time"):
            buffer.local_due(value, "Europe/Helsinki")
    with pytest.raises(ValueError, match="future"):
        buffer.preview(settings, store, [item()], "schedule", "Europe/Helsinki", "2020-01-01T18:00")


def test_refresh_tracks_provider_errors_and_cloud_removal_waits_for_publication(settings, store, remote, monkeypatch):
    ticket = buffer.preview(settings, store, [item()], "now")
    buffer.send(settings, store, ticket["id"], "early")
    remote["posts"]["1"].update(status="error", error={"message": "Reconnect account"})
    remote["posts"]["2"].update(schedulingType="notification")
    buffer.refresh(settings, store)
    receipts = buffer.publications(store)["early"]["receipts"]
    assert receipts["youtube"]["status"] == "error"
    assert receipts["instagram"]["status"] == "attention"
    with pytest.raises(ValueError, match="every tracked platform"):
        buffer.remove_published_media(settings, store, "early")
    for post in remote["posts"].values():
        post.update(status="sent", schedulingType="automatic", error=None)
    buffer.refresh(settings, store)
    with pytest.raises(ValueError, match="48 hours"):
        buffer.remove_published_media(settings, store, "early")
    later = buffer.now() + timedelta(hours=49)
    monkeypatch.setattr(buffer, "now", lambda: later)
    assert buffer.remove_published_media(settings, store, "early") == {"removed": True}
    assert not remote["objects"] and not r2.uploads(settings)
    assert (settings.ready / "early" / "1" / "short.mp4").exists()


def test_invalid_credentials_are_never_echoed(settings, monkeypatch):
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    api = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": api.get("/api/status").json()["token"]}
    secret = "sensitive invalid key"
    response = api.post("/api/publishing/buffer/connect", headers=headers, json={"api_key": secret})
    assert response.status_code == 400 and secret not in response.text


def test_reads_back_off_but_post_creation_is_never_blindly_retried(monkeypatch):
    calls, delays = [], []
    original = httpx.Client

    def respond(request):
        calls.append(request)
        return httpx.Response(502)

    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(buffer.time, "sleep", delays.append)
    api = buffer.Client("test")
    with pytest.raises(buffer.RemoteError, match="502"):
        api.query("query test")
    assert len(calls) == 3 and delays == [2, 5]
    calls.clear()
    with pytest.raises(buffer.RemoteError, match="502"):
        api.query("mutation test", mutation=True)
    assert len(calls) == 1


def publish_cleanup_pair(settings, store, remote):
    for clip in ("early", "late"):
        ticket = buffer.preview(settings, store, [item(clip)], "now")
        buffer.send(settings, store, ticket["id"], clip)
    for post in remote["posts"].values():
        post["status"] = "sent"
    buffer.refresh(settings, store)


def test_bulk_cloud_cleanup_refreshes_skips_pending_and_is_repeatable(settings, store, remote, monkeypatch):
    publish_cleanup_pair(settings, store, remote)
    late = buffer.publications(store)["late"]
    for receipt in late["receipts"].values():
        receipt["status"] = "scheduled"
        remote["posts"][receipt["post_id"]]["status"] = "scheduled"
    buffer.save_publication(store, late)
    later = buffer.now() + timedelta(hours=49)
    monkeypatch.setattr(buffer, "now", lambda: later)
    api = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": api.get("/api/status").json()["token"]}
    endpoint = "/api/publishing/buffer/media-cleanup"
    assert api.post(endpoint).status_code == 403
    response = api.post(endpoint, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [r["clip_id"] for r in result["removed"]] == ["early"]
    assert [r["clip_id"] for r in result["kept"]] == ["late"]
    assert result["removed_bytes"] == len(b"final-early") and not result["failed"]
    assert list(remote["objects"].values()) == [b"final-late"]
    assert buffer.publications(store)["early"]["media_removed_at"]
    assert (settings.ready / "early/1/short.mp4").read_bytes() == b"final-early"
    assert all(r["status"] == "posted" for r in buffer.publications(store)["early"]["receipts"].values())
    result = api.post(endpoint, headers=headers).json()
    assert result["removed"] == [] and len(result["kept"]) == 1
    assert len([r for r in remote["requests"] if r.method == "DELETE"]) == 1


def test_bulk_cleanup_newly_confirmed_posts_wait_and_untracked_uploads_stay(settings, store, remote):
    ticket = buffer.preview(settings, store, [item()], "now")
    buffer.send(settings, store, ticket["id"], "early")
    r2.upload(settings, store, "late", 1)
    for post in remote["posts"].values():
        post["status"] = "sent"
    result = buffer.remove_all_published_media(settings, store)
    assert not result["removed"] and not result["failed"]
    assert len(result["kept"]) == 2
    assert "48 hours" in next(r["reason"] for r in result["kept"] if r["clip_id"] == "early")
    assert "no confirmed publication" in next(r["reason"] for r in result["kept"] if r["clip_id"] == "late")
    assert all(r["status"] == "posted" for r in buffer.publications(store)["early"]["receipts"].values())
    assert len(remote["objects"]) == 2


def test_bulk_cleanup_preserves_file_reused_directly_in_buffer(settings, store, remote, monkeypatch):
    publish_cleanup_pair(settings, store, remote)
    remote["posts"]["external"] = {**remote["posts"]["1"], "id": "external", "status": "draft"}
    later = buffer.now() + timedelta(hours=49)
    monkeypatch.setattr(buffer, "now", lambda: later)
    result = buffer.remove_all_published_media(settings, store)
    assert [r["clip_id"] for r in result["removed"]] == ["late"]
    assert [r["clip_id"] for r in result["kept"]] == ["early"]
    assert "Buffer post still uses" in result["kept"][0]["reason"]


def test_bulk_cleanup_deletes_shared_file_once_and_retains_every_receipt(settings, store, remote, monkeypatch):
    import copy

    ticket = buffer.preview(settings, store, [item()], "now")
    original = buffer.send(settings, store, ticket["id"], "early")
    for post in remote["posts"].values():
        post["status"] = "sent"
    buffer.refresh(settings, store)
    shared = copy.deepcopy(buffer.publications(store)["early"])
    shared["clip_id"] = "late"
    buffer.save_publication(store, shared)
    later = buffer.now() + timedelta(hours=49)
    monkeypatch.setattr(buffer, "now", lambda: later)
    result = buffer.remove_all_published_media(settings, store)
    assert len(result["removed"]) == 1 and not result["kept"]
    assert result["removed_bytes"] == original["media"]["bytes"]
    assert all(j["media_removed_at"] for j in buffer.publications(store).values())
    assert not remote["objects"]
    assert len([r for r in remote["requests"] if r.method == "DELETE"]) == 1


def test_bulk_cleanup_continues_after_one_cloud_delete_fails(settings, store, remote, monkeypatch):
    publish_cleanup_pair(settings, store, remote)
    later = buffer.now() + timedelta(hours=49)
    monkeypatch.setattr(buffer, "now", lambda: later)
    early_hash = buffer.publications(store)["early"]["media"]["sha256"]
    original_request = r2.Client.request
    def request(self, method, key="", **kwargs):
        if method == "DELETE" and early_hash in key:
            raise ValueError("Cloudflare could not be reached.")
        return original_request(self, method, key, **kwargs)
    monkeypatch.setattr(r2.Client, "request", request)
    result = buffer.remove_all_published_media(settings, store)
    assert [r["clip_id"] for r in result["removed"]] == ["late"]
    assert [r["clip_id"] for r in result["failed"]] == ["early"]
    assert not buffer.publications(store)["early"].get("media_removed_at")
    assert len(r2.uploads(settings)) == 1
    assert list(remote["objects"].values()) == [b"final-early"]


def test_bulk_cleanup_stops_before_deletion_when_buffer_check_fails(settings, store, remote, monkeypatch):
    publish_cleanup_pair(settings, store, remote)
    def unavailable(*args):
        raise buffer.RemoteError("Buffer unavailable")
    monkeypatch.setattr(buffer.Client, "organizations", unavailable)
    with pytest.raises(buffer.RemoteError, match="unavailable"):
        buffer.remove_all_published_media(settings, store)
    assert not any(r.method == "DELETE" for r in remote["requests"])
    assert len(remote["objects"]) == 2


def test_bulk_cleanup_respects_publishing_and_upload_locks(settings, remote):
    from vaarattu_shorts.processes import lock

    api = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    headers = {"x-local-token": api.get("/api/status").json()["token"]}
    for name in ("buffer-publishing.lock", "r2-upload.lock"):
        with lock(settings.work / name):
            response = api.post("/api/publishing/buffer/media-cleanup", headers=headers)
        assert response.status_code == 400 and "in progress" in response.text
    assert not any(r.method == "DELETE" for r in remote["requests"])


def test_publication_time_is_saved_and_legacy_backfill_keeps_cleanup_confirmation(settings, store, remote, monkeypatch):
    ticket = buffer.preview(settings, store, [item()], "now")
    buffer.send(settings, store, ticket["id"], "early")
    for post in remote["posts"].values():
        post.update(status="sent", sentAt="2026-09-29T16:12:00Z")
    buffer.refresh(settings, store)
    job = buffer.publications(store)["early"]
    assert all(r["published_at"] == "2026-09-29T16:12:00Z" for r in job["receipts"].values())
    confirmed = {p: r["checked_at"] for p, r in job["receipts"].items()}
    for receipt in job["receipts"].values():
        del receipt["published_at"]
    buffer.save_publication(store, job)
    later = buffer.now() + timedelta(hours=49)
    monkeypatch.setattr(buffer, "now", lambda: later)
    buffer.refresh(settings, store)
    saved = buffer.publications(store)["early"]
    assert all(r["published_at"] == "2026-09-29T16:12:00Z" for r in saved["receipts"].values())
    assert {p: r["checked_at"] for p, r in saved["receipts"].items()} == confirmed
    assert not buffer.media_removal_reason([saved])
    count = sum(b"query Post(" in request.content for request in remote["requests"])
    buffer.refresh(settings, store)
    assert sum(b"query Post(" in request.content for request in remote["requests"]) == count


def test_calendar_keeps_manual_post_history_after_final_approval_changes(settings, store, remote):
    delivery.schedule(store, [remote["run"]], "2030-01-01", "18:00", "Europe/Helsinki")
    post = next(p for p in delivery.posting_plan(store) if p["clip_id"] == "early")
    delivery.record_post(store, post["id"], "youtube", "https://youtube.com/shorts/published")
    store.review_clip("early", 1, "approved")
    clip = next(c for c in buffer.status(settings, store)["clips"] if c["id"] == "early")
    assert not clip["ready"] and clip["publication"] is None
    assert clip["plan"]["deliveries"]["youtube"]["status"] == "posted"
