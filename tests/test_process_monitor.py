import stat
from types import SimpleNamespace

import pytest

from vaarattu_shorts.processes import OwnedProcess, ToolError


def process(tmp_path):
    value = OwnedProcess.__new__(OwnedProcess)
    polls = iter([None, 0])
    value.proc = SimpleNamespace(poll=lambda: next(polls), returncode=0)
    value.log = tmp_path / "absent.log"
    return value


def test_download_rename_during_size_check_does_not_crash(tmp_path, monkeypatch):
    def renamed():
        raise FileNotFoundError("temporary download renamed")

    temporary = SimpleNamespace(is_file=lambda: True, stat=renamed)
    final = SimpleNamespace(
        is_file=lambda: True, stat=lambda: SimpleNamespace(st_size=50, st_mode=stat.S_IFREG)
    )
    watch = SimpleNamespace(rglob=lambda _: iter([temporary, final]))
    monkeypatch.setattr("vaarattu_shorts.processes.time.sleep", lambda _: None)
    process(tmp_path).wait(byte_limit=100, watch=watch)


def test_download_monitor_still_enforces_limit_and_counts_only_files(tmp_path, monkeypatch):
    final = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=101, st_mode=stat.S_IFREG))
    directory = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=9999, st_mode=stat.S_IFDIR))
    monkeypatch.setattr("vaarattu_shorts.processes.time.sleep", lambda _: None)
    watch = SimpleNamespace(rglob=lambda _: iter([directory]))
    process(tmp_path).wait(byte_limit=100, watch=watch)
    watch = SimpleNamespace(rglob=lambda _: iter([final]))
    with pytest.raises(ToolError, match="size limit"):
        process(tmp_path).wait(byte_limit=100, watch=watch)


def test_download_monitor_does_not_hide_access_errors(tmp_path):
    def denied():
        raise PermissionError("cannot inspect download")

    watch = SimpleNamespace(rglob=lambda _: iter([SimpleNamespace(stat=denied)]))
    with pytest.raises(PermissionError):
        process(tmp_path).wait(byte_limit=100, watch=watch)
