import socket
import subprocess

import pytest

from vaarattu_shorts.config import Settings
from vaarattu_shorts.storage import Store


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


@pytest.fixture
def store(settings):
    return Store(settings.work / "state.sqlite3")
