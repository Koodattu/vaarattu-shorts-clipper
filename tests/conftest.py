import shutil
import socket
import subprocess
import tempfile

import pytest

from vaarattu_shorts.config import Settings
from vaarattu_shorts.storage import Store


def pytest_configure(config):
    # Keep pytest's numbered runs and application-created temp files on the project drive.
    scratch = config.rootpath / ".cache" / "tests"
    scratch.mkdir(parents=True, exist_ok=True)
    previous = tempfile.tempdir
    tempfile.tempdir = str(scratch)
    config.add_cleanup(lambda: setattr(tempfile, "tempdir", previous))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_pair = socket.socketpair

    def internal_pair(*args, **kwargs):
        # Windows asyncio creates its internal wake-up pair using loopback TCP.
        # Restore connects only inside the standard-library socketpair constructor.
        with monkeypatch.context() as pair_patch:
            pair_patch.setattr(socket.socket, "connect", original_connect)
            pair_patch.setattr(socket.socket, "connect_ex", original_connect_ex)
            return original_pair(*args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "socketpair", internal_pair)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def settings(tmp_path):
    settings = Settings(root=tmp_path, min_free_gb=0)
    settings.initialize()
    return settings


@pytest.fixture(scope="session")
def empty_database(tmp_path_factory):
    path = tmp_path_factory.mktemp("schema") / "state.sqlite3"
    Store(path)
    return path


@pytest.fixture
def store(settings, empty_database):
    # Each test gets its own real SQLite file, initialized from the same closed, empty database.
    path = settings.work / "state.sqlite3"
    shutil.copyfile(empty_database, path)
    return Store(path)
