from __future__ import annotations

import signal
import time
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from .pipeline import Pipeline
from .processes import Interrupted, ToolError, lock
from .storage import Store, atomic_json


def run_job(settings, store, run_id, stopping):
    try:
        pipeline = Pipeline(settings, store, run_id, stopping)
        if store.get(run_id)["result"].get("review_all_requested"):
            pipeline.review_all()
        elif store.get(run_id)["result"].get("context_repair_requested"):
            pipeline.repair_context()
        elif store.get(run_id)["result"].get("selection_recheck_requested"):
            pipeline.recheck_selection()
        elif store.get(run_id)["result"].get("rerender_requested") or store.get(run_id)["stage"] == "rerender":
            pipeline.rerender()
        else:
            pipeline.execute()
    except Interrupted as exc:
        store.update(
            run_id,
            state="cancelled" if exc.action == "cancel" else "paused",
            intent="",
            message="Completed stages are saved.",
        )
    except (ValueError, ToolError) as exc:
        store.update(run_id, state="failed", message=str(exc), intent="")
    except Exception as exc:
        # Save locations and exception type only, never messages, source lines or locals.
        diagnostic = settings.work / "runs" / run_id / "worker-error.json"
        try:
            atomic_json(
                diagnostic,
                {
                    "stage": store.get(run_id)["stage"],
                    "exception_type": type(exc).__name__,
                    "frames": [
                        {
                            "file": Path(frame.filename).name,
                            "function": frame.name,
                            "line": frame.lineno,
                        }
                        for frame in traceback.extract_tb(exc.__traceback__)
                    ],
                },
            )
            note = " Details saved in worker-error.json."
        except OSError:
            note = " The diagnostic file could not be saved."
        store.update(
            run_id,
            state="failed",
            message="Processing stopped unexpectedly. Completed stages are saved." + note,
            intent="",
        )


def work(settings, once=False):
    settings.initialize()
    store = Store(settings.work / "state.sqlite3")
    stopping = Event()

    def stop(*_):
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    with lock(settings.work / "worker.lock"):
        store.recover()
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="clip-job") as pool:
            active = set()
            try:
                while not stopping.is_set():
                    for future in tuple(active):
                        if future.done():
                            future.result()
                            active.remove(future)
                    while len(active) < 4 and not stopping.is_set():
                        run_id = store.claim()
                        if run_id is None:
                            break
                        future = pool.submit(run_job, settings, store, run_id, stopping.is_set)
                        active.add(future)
                        if once:
                            future.result()
                            return
                    if once:
                        return
                    time.sleep(0.2)
            finally:
                stopping.set()
