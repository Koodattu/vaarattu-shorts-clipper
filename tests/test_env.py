import os

import pytest
from fastapi.testclient import TestClient

from vaarattu_shorts.config import load_env, load_settings
from vaarattu_shorts.web import create_app


def test_project_env_loaded_outside_working_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text('GEMINI_API_KEY="fixture-private-value"\n', encoding="utf-8-sig")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=wrong-directory\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = load_settings(project)
    assert settings.environment()["GEMINI_API_KEY"] == "fixture-private-value"
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/status")
        assert response.json()["providers"]["gemini"]["configured"]
        assert "fixture-private-value" not in response.text
        assert client.get("/.env").status_code == 404


def test_env_literal_values_comments_and_process_precedence(tmp_path, monkeypatch):
    for key in ("CLIPPER_TEST_SINGLE", "CLIPPER_TEST_LITERAL", "CLIPPER_TEST_EMPTY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLIPPER_TEST_EXISTING", "process-value")
    path = tmp_path / ".env"
    path.write_text(
        "# a comment\n\n"
        "export CLIPPER_TEST_SINGLE = 'token#with=punctuation' # comment\n"
        'CLIPPER_TEST_LITERAL="${HOME} $(command) C:\\path"\n'
        "CLIPPER_TEST_EXISTING=file-value # comment\n"
        "CLIPPER_TEST_EMPTY= # not configured\n",
        encoding="utf-8",
    )
    load_env(path)
    assert os.environ["CLIPPER_TEST_SINGLE"] == "token#with=punctuation"
    assert os.environ["CLIPPER_TEST_LITERAL"] == "${HOME} $(command) C:\\path"
    assert os.environ["CLIPPER_TEST_EXISTING"] == "process-value"
    assert os.environ["CLIPPER_TEST_EMPTY"] == ""


@pytest.mark.parametrize(
    "entry",
    [
        "invalid fixture-private-value",
        'CLIPPER_TEST_BAD="fixture-private-value',
        'CLIPPER_TEST_BAD="fixture-private-value" trailing',
    ],
)
def test_invalid_env_does_not_leak_values_or_load_partially(tmp_path, monkeypatch, entry):
    monkeypatch.delenv("CLIPPER_TEST_FIRST", raising=False)
    path = tmp_path / ".env"
    path.write_text("CLIPPER_TEST_FIRST=value\n" + entry, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_env(path)
    assert "line 2" in str(exc.value)
    assert "fixture-private-value" not in str(exc.value)
    assert "CLIPPER_TEST_FIRST" not in os.environ


def test_env_optional_and_empty_process_value_is_preserved(tmp_path, monkeypatch):
    load_env(tmp_path / "missing.env")
    monkeypatch.setenv("CLIPPER_TEST_EMPTY", "")
    path = tmp_path / ".env"
    path.write_text("CLIPPER_TEST_EMPTY=file-value", encoding="utf-8")
    load_env(path)
    assert os.environ["CLIPPER_TEST_EMPTY"] == ""
