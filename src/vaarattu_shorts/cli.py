from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path

from .config import load_settings


def serve_web(settings):
    import uvicorn

    from .web import create_app

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host="127.0.0.1",
            port=settings.port,
            log_level="info",
            access_log=False,
            timeout_graceful_shutdown=5,
        )
    )
    # Python's Windows proactor can fail before detaching a reset connection,
    # leaving server.wait_closed() stuck. The UI needs only socket-based I/O;
    # media subprocesses run separately in the owned worker.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(server.serve())
    except KeyboardInterrupt:
        pass
    if not server.started:
        raise SystemExit(3)


def main():
    parser = argparse.ArgumentParser(description="Vaarattu's local single-video shorts pipeline")
    parser.add_argument("--project", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Start local UI and a separate durable worker")
    sub.add_parser("web", help="Start only the local UI")
    worker = sub.add_parser("worker", help="Start only the worker")
    worker.add_argument("--once", action="store_true")
    sub.add_parser("status", help="Show local setup and saved runs without processing")
    models = sub.add_parser("models", help="Explicitly prepare a model inside this project's .cache/models")
    models.add_argument("model", choices=["turbo", "gemma4-31b", "gemma4-26b-a4b"])
    sub.add_parser("backup", help="Back up state and durable project artifacts")
    args = parser.parse_args()
    settings = load_settings(args.project)
    os.environ.update(settings.environment())
    os.environ["VAARATTU_PROJECT"] = str(settings.root)
    # Tool paths in config.toml are interpreted relative to the project, never a job directory.
    from dataclasses import replace

    settings = replace(
        settings,
        **{
            name: str((settings.root / getattr(settings, name)).resolve())
            for name in ("ffmpeg", "ffprobe", "llama_server")
            if "/" in getattr(settings, name) or "\\" in getattr(settings, name)
        },
    )
    if args.command == "models":
        from .models import prepare

        print(prepare(settings, args.model))
        return
    settings.initialize()
    if args.command == "status":
        from .models import CATALOG, model_path
        from .storage import Store

        status = {}
        for key in CATALOG:
            try:
                status[key] = str(model_path(settings, key))
            except ValueError:
                status[key] = "not prepared"
        print(
            json.dumps(
                {
                    "models": status,
                    "cache": str(settings.cache),
                    "runs": Store(settings.work / "state.sqlite3").runs(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "backup":
        from .maintenance import backup

        print(backup(settings))
        return
    if args.command == "worker":
        from .worker import work

        work(settings, args.once)
        return
    if args.command == "web":
        serve_web(settings)
        return
    from .processes import LockBusyError, OwnedProcess, lock

    with ExitStack() as stack:
        try:
            stack.enter_context(lock(settings.work / "launcher.lock"))
        except LockBusyError:
            print(
                f"Vaarattu Shorts is already running for this project. Open http://127.0.0.1:{settings.port}"
            )
            return
        with OwnedProcess(
            [sys.executable, "-m", "vaarattu_shorts", "--project", settings.root, "worker"],
            cwd=settings.root,
            env=settings.environment(),
            log=settings.work / "logs" / "worker.log",
        ):
            serve_web(settings)


if __name__ == "__main__":
    main()
