import asyncio
import signal
import sys
from contextlib import asynccontextmanager, contextmanager

import pytest
import uvicorn
from fastapi import FastAPI

from vaarattu_shorts import cli
from vaarattu_shorts.processes import lock


@pytest.mark.parametrize("command", ["web", "serve"])
def test_ctrl_c_cancels_stuck_request_and_releases_worker(settings, monkeypatch, command):
    cancelled = []
    worker_events = []
    forced = []

    def force_stop():
        forced.append(True)
        signal.raise_signal(signal.SIGINT)

    async def stuck_request():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def startup(server, sockets=None):
        await server.lifespan.startup()
        server.servers = []
        server.started = True
        loop = asyncio.get_running_loop()
        if sys.platform == "win32":
            assert isinstance(loop, asyncio.SelectorEventLoop)
        request = loop.create_task(stuck_request())
        server.server_state.tasks.add(request)
        request.add_done_callback(server.server_state.tasks.discard)
        loop.call_later(0.01, signal.raise_signal, signal.SIGINT)
        loop.call_later(8, force_stop)

    @contextmanager
    def worker(*args, **kwargs):
        worker_events.append("started")
        try:
            yield
        finally:
            worker_events.append("stopped")

    monkeypatch.setattr(cli, "load_settings", lambda _: settings)
    monkeypatch.setattr(sys, "argv", ["vaarattu-shorts", command])
    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    monkeypatch.setattr("vaarattu_shorts.processes.OwnedProcess", worker)
    original_handler = signal.getsignal(signal.SIGINT)

    cli.main()

    assert cancelled == [True]
    assert forced == []
    assert signal.getsignal(signal.SIGINT) == original_handler
    assert worker_events == (["started", "stopped"] if command == "serve" else [])
    with lock(settings.work / "launcher.lock"):
        pass


def test_startup_failure_still_returns_failure(settings, monkeypatch):
    @asynccontextmanager
    async def lifespan(app):
        raise RuntimeError("Startup failed")
        yield

    monkeypatch.setattr("vaarattu_shorts.web.create_app", lambda _: FastAPI(lifespan=lifespan))
    with pytest.raises(SystemExit) as exc:
        cli.serve_web(settings)
    assert exc.value.code == 3
