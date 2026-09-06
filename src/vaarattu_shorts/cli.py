from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import load_settings


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
    import uvicorn
    from .web import create_app

    if args.command == "web":
        uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port, log_level="warning")
        return
    from .processes import OwnedProcess, lock

    with lock(settings.work / "launcher.lock"):
        with OwnedProcess(
            [sys.executable, "-m", "vaarattu_shorts", "--project", settings.root, "worker"],
            cwd=settings.root,
            env=settings.environment(),
            log=settings.work / "logs" / "worker.log",
        ):
            uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
