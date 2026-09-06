from __future__ import annotations

import shutil
import sqlite3
import time

from .processes import lock
from .storage import atomic_json, digest


def backup(settings):
    # A consistent DB + artifact set requires the worker to be stopped.
    with lock(settings.work / "worker.lock"):
        target = settings.work / "backups" / time.strftime("%Y%m%d-%H%M%S")
        target.mkdir(parents=True, exist_ok=False)
        with sqlite3.connect(settings.work / "state.sqlite3") as source:
            with sqlite3.connect(target / "state.sqlite3") as dest:
                source.backup(dest)
                if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("The database backup failed its integrity check.")
        for name in ("runs", "ready"):
            shutil.copytree(
                settings.work / name,
                target / name,
                ignore=shutil.ignore_patterns("*.wav", "*.log", "audio", "sections"),
            )
        atomic_json(
            target / "manifest.json",
            {
                "files": [
                    {"path": str(p.relative_to(target)), "sha256": digest(p)}
                    for p in target.rglob("*")
                    if p.is_file()
                ]
            },
        )
        return target
