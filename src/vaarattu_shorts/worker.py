from __future__ import annotations

import signal
import time
import traceback
from pathlib import Path
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Thread

from . import highlights
from .pipeline import Pipeline
from .processes import Interrupted, ToolError, lock
from .storage import Store, atomic_json


def run_job(settings, store, run_id, stopping):
    try:
        pipeline = Pipeline(settings, store, run_id, stopping)
        if store.get(run_id)["result"].get("tighten_requested"):
            pipeline.suggest_tighter_edit()
        elif store.get(run_id)["result"].get("caption_check_requested"):
            pipeline.check_captions()
        elif store.get(run_id)["result"].get("review_all_requested"):
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
        highlight_store = highlights.store_for(settings)
        highlight_store.recover()
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="clip-job") as pool, ThreadPoolExecutor(max_workers=1, thread_name_prefix="highlight-job") as highlight_pool:
            active = set()
            highlight_job = None
            reviews = {}

            def review_job(future, run_id):
                try:
                    future.set_result(run_job(settings, store, run_id, stopping.is_set))
                except BaseException as exc:
                    future.set_exception(exc)

            try:
                while not stopping.is_set():
                    if highlight_job is not None and highlight_job.done():
                        highlight_job.result()
                        highlight_job = None
                    if highlight_job is None:
                        highlight_id = highlight_store.claim(review=False)
                        if highlight_id:
                            highlight_job = highlight_pool.submit(highlights.run_job, settings, highlight_store, highlight_id, stopping.is_set)
                            if once:
                                highlight_job.result()
                                return
                    for future, thread in tuple(reviews.items()):
                        if future.done():
                            thread.join()
                            future.result()
                            del reviews[future]
                    for future in tuple(active):
                        if future.done():
                            future.result()
                            active.remove(future)
                    # Interactive work must not wait behind the VOD pool or its limit.
                    while not stopping.is_set():
                        run_id = store.claim(review=True)
                        if run_id is None:
                            break
                        future = Future()
                        thread = Thread(target=review_job, args=(future, run_id), name=f"clip-review-{run_id}")
                        reviews[future] = thread
                        thread.start()
                        if once:
                            future.result()
                            return
                    while len(active) < 4 and not stopping.is_set():
                        run_id = store.claim(review=False)
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
                for thread in reviews.values():
                    thread.join()
