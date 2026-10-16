from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts import buffer, delivery
from vaarattu_shorts.processes import lock
from vaarattu_shorts.web import create_app
from test_buffer import item, remote as remote


URL = "https://www.tiktok.com/@vaarattu/video/123456789"


def failed_post(settings, store, remote):
    delivery.schedule(store, [remote["run"]], "2030-10-25", "18:00", "Europe/Helsinki")
    ticket = buffer.preview(settings, store, [item()], "plan")
    buffer.send(settings, store, ticket["id"], "early")
    for post in remote["posts"].values():
        post.update(status="sent", sentAt="2030-10-25T15:01:00Z")
    remote["posts"]["3"].update(status="error", sentAt=None, error={"message": "socket hang up"})
    buffer.refresh(settings, store)
    return buffer.publications(store)["early"]


def test_confirm_published_route_preserves_audit_and_survives_sync(settings, store, remote, monkeypatch):
    before = failed_post(settings, store, remote)
    api = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    endpoint = "/api/publishing/buffer/confirm-published/early/tiktok"
    body = {"url": URL, "post_id": "3"}
    headers = {"x-local-token": api.get("/api/status").json()["token"]}
    assert api.post(endpoint, json=body).status_code == 403
    with lock(settings.work / "buffer-publishing.lock"):
        assert api.post(endpoint, headers=headers, json=body).status_code == 400
    request_count = len(remote["requests"])
    response = api.post(endpoint, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert len(remote["requests"]) == request_count, "Confirmation must not contact the social service"
    job = response.json()
    saved = job["receipts"]["tiktok"]
    assert saved["status"] == "posted" and saved["url"] == URL
    assert saved["published_at"] is None, "Do not invent a publication timestamp"
    assert saved["manual_confirmation"]["previous_receipt"] == before["receipts"]["tiktok"]
    assert saved["manual_confirmation"]["confirmed_at"] == saved["checked_at"]
    assert job["receipts"]["youtube"] == before["receipts"]["youtube"]
    assert job["receipts"]["instagram"] == before["receipts"]["instagram"]
    assert remote["posts"]["3"]["status"] == "error"
    plan = next(p for p in delivery.posting_plan(store) if p["clip_id"] == "early")
    assert plan["deliveries"]["tiktok"] == {**saved, "method": "buffer"}
    later = buffer.now() + timedelta(hours=1)
    monkeypatch.setattr(buffer, "now", lambda: later)
    assert api.post(endpoint, headers=headers, json=body).json()["receipts"]["tiktok"] == saved
    buffer.refresh(settings, store)
    assert buffer.publications(store)["early"] == job
    assert len(remote["mutations"]) == 3


@pytest.mark.parametrize("url", ["", "http://www.tiktok.com/@a/video/1", "https://instagram.com/reel/1",
                                  "https://tiktok.com.evil.test/@a/video/1", "https://user@tiktok.com/@a/video/1"])
def test_confirmation_rejects_invalid_platform_links_without_changing_receipt(settings, store, remote, url):
    before = failed_post(settings, store, remote)
    with pytest.raises(ValueError, match="HTTPS link"):
        buffer.confirm_published(store, "early", "tiktok", url, "3")
    assert buffer.publications(store)["early"] == before


@pytest.mark.parametrize("status", ["pending", "scheduled", "sending", "draft", "needs_approval", "posted"])
def test_confirmation_cannot_override_active_or_successful_receipts(settings, store, remote, status):
    job = failed_post(settings, store, remote)
    job["receipts"]["tiktok"]["status"] = status
    buffer.save_publication(store, job)
    with pytest.raises(ValueError, match="failed or uncertain"):
        buffer.confirm_published(store, "early", "tiktok", URL, "3")
    assert buffer.publications(store)["early"] == job


def test_confirmation_rejects_stale_post_and_supports_tiktok_share_links(settings, store, remote):
    before = failed_post(settings, store, remote)
    with pytest.raises(ValueError, match="receipt changed"):
        buffer.confirm_published(store, "early", "tiktok", URL, "old-id")
    assert buffer.publications(store)["early"] == before
    link = "https://vm.tiktok.com/abc123/"
    assert buffer.confirm_published(store, "early", "tiktok", link, "3")["receipts"]["tiktok"]["url"] == link


def test_manual_confirmation_cleanup_waits_and_only_bypasses_exact_failed_post(settings, store, remote, monkeypatch):
    failed_post(settings, store, remote)
    buffer.confirm_published(store, "early", "tiktok", URL, "3")
    result = buffer.remove_all_published_media(settings, store)
    assert not result["removed"] and "48 hours" in result["kept"][0]["reason"]
    later = buffer.now() + timedelta(hours=49)
    monkeypatch.setattr(buffer, "now", lambda: later)
    # A retry of the same remote post still requires the file.
    remote["posts"]["3"]["status"] = "scheduled"
    assert buffer.remove_all_published_media(settings, store)["kept"]
    remote["posts"]["3"]["status"] = "error"
    # A different failed post sharing the file also still protects it.
    remote["posts"]["other"] = {**remote["posts"]["3"], "id": "other"}
    assert buffer.remove_all_published_media(settings, store)["kept"]
    del remote["posts"]["other"]
    result = buffer.remove_all_published_media(settings, store)
    assert [r["clip_id"] for r in result["removed"]] == ["early"]
    assert not result["kept"] and not result["failed"]
    assert not remote["objects"]
    assert (settings.ready / "early/1/short.mp4").exists()
    assert buffer.publications(store)["early"]["receipts"]["tiktok"]["manual_confirmation"]
    assert len(remote["mutations"]) == 3
