import errno
from pathlib import Path

import pytest

from vaarattu_shorts import cli
from vaarattu_shorts.processes import LockBusyError, lock


def test_held_lock_is_distinct_and_released(tmp_path):
    path = tmp_path / "launcher.lock"
    with lock(path):
        with pytest.raises(LockBusyError):
            with lock(path):
                pytest.fail("A second launcher acquired the lock")
    with lock(path):
        pass


def test_duplicate_launcher_exits_before_starting_worker(settings, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda _: settings)
    monkeypatch.setattr("sys.argv", ["vaarattu-shorts", "serve"])
    with lock(settings.work / "launcher.lock"):
        cli.main()
    output = capsys.readouterr()
    assert "already running" in output.out and "http://127.0.0.1:8765" in output.out
    assert not output.err


def test_file_access_failure_is_not_reported_as_already_running(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Access denied")

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(PermissionError):
        with lock(tmp_path / "launcher.lock"):
            pass
