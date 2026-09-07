import base64
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from vaarattu_shorts.contracts import Layout
from vaarattu_shorts.storage import Store
from vaarattu_shorts.web import create_app


def preset(**changes):
    return {
        "name": "Main camera",
        "camera": {"x": 0, "y": 0, "width": 0.3, "height": 0.3},
        "gameplay": {"x": 0.3, "y": 0.3, "width": 0.7, "height": 0.7},
        **changes,
    }


def test_same_name_save_replaces_duplicates_and_preserves_snapshots(store):
    original = preset()
    layout_id = store.add_layout(original)
    run_id = store.admit({"layout": original, "layout_id": layout_id}, "snapshot")
    store.save_clip("clip", run_id, 1, {"layout": original})
    with store.connect() as db:
        db.execute("INSERT INTO layouts VALUES(?,?)", ("duplicate", json.dumps(original)))
    changed = preset(name=" MAIN CAMERA ", camera_height=960)
    assert store.add_layout(changed) == layout_id
    assert len(store.layouts()) == 1
    assert store.layout(layout_id)["camera_height"] == 960
    store.delete_layout(layout_id)
    assert store.layouts() == []
    assert store.get(run_id)["config"]["layout"] == original
    assert store.clip("clip")["body"]["layout"] == original
    with pytest.raises(KeyError):
        store.delete_layout(layout_id)


def test_concurrent_same_name_save_keeps_one_id(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda height: store.add_layout(preset(camera_height=height)), [400, 600, 800, 960]))
    assert len(set(ids)) == 1
    assert len(store.layouts()) == 1


@pytest.mark.parametrize("changes", [
    {"name": "  "}, {"camera_height": 607}, {"camera_height": 190},
    {"camera_height": 1730}, {"camera_fit": "stretch"}, {"gameplay_ratio": "invalid"},
])
def test_invalid_layout_settings(changes):
    with pytest.raises(ValidationError):
        Layout.model_validate(preset(**changes))


def test_legacy_layout_defaults():
    layout = Layout.model_validate(preset())
    assert layout.camera_height == 608
    assert layout.camera_fit == layout.gameplay_fit == "cover"
    assert layout.camera_ratio == layout.gameplay_ratio == "panel"


def test_screenshot_save_reload_overwrite_delete_and_local_boundary(settings):
    app = create_app(settings)
    image = b"\xff\xd8\xff\xe0test image\xff\xd9"
    screenshot = {"name": "source.jpg", "data": "data:image/jpeg;base64," + base64.b64encode(image).decode()}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"X-Local-Token": client.get("/api/status").json()["token"]}
        response = client.post("/api/layouts", json={**preset(), "screenshot": screenshot}, headers=headers)
        assert response.status_code == 201
        layout_id = response.json()["id"]
        assert client.get(f"/api/layouts/{layout_id}/screenshot").content == image
        row = client.get("/api/layouts").json()[0]
        assert row["screenshot_name"] == "source.jpg"
        assert "screenshot" not in row["body"]
        # A fresh store connection sees the persisted image; coordinate-only saves retain it.
        assert Store(settings.work / "state.sqlite3").layout_frame(layout_id) == image
        response = client.post("/api/layouts", json=preset(camera_height=960), headers=headers)
        assert response.json()["id"] == layout_id
        assert client.get(f"/api/layouts/{layout_id}/screenshot").content == image
        replacement = {**screenshot, "name": "replacement.jpg"}
        assert client.post("/api/layouts", json={**preset(), "screenshot": replacement}, headers=headers).status_code == 201
        assert client.get("/api/layouts").json()[0]["screenshot_name"] == "replacement.jpg"
        assert client.delete(f"/api/layouts/{layout_id}").status_code == 403
        assert client.delete(f"/api/layouts/{layout_id}", headers=headers).status_code == 200
        assert client.get(f"/api/layouts/{layout_id}/screenshot").status_code == 404
        assert client.delete(f"/api/layouts/{layout_id}", headers=headers).status_code == 404
        assert client.get("/api/layouts").json() == []
        bad = {**screenshot, "data": "data:image/jpeg;base64,broken"}
        assert client.post("/api/layouts", json={**preset(), "screenshot": bad}, headers=headers).status_code == 400
        assert client.get("/api/layouts").json() == []
