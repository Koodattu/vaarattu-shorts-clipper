from __future__ import annotations

import signal
import time

from .pipeline import Pipeline
from .processes import Interrupted, ToolError, lock
from .storage import Store


def work(settings, once=False):
    settings.initialize()
    store = Store(settings.work / "state.sqlite3")
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    with lock(settings.work / "worker.lock"):
        store.recover()
        while not stopping:
            run_id = store.claim()
            if run_id:
                try:
                    pipeline = Pipeline(settings, store, run_id, lambda: stopping)
                    if store.get(run_id)["stage"] == "rerender":
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
                except Exception:
                    # Untrusted provider errors can contain keys or signed URLs; never return raw exceptions.
                    store.update(
                        run_id,
                        state="failed",
                        message="Processing stopped unexpectedly. Completed stages are saved.",
                        intent="",
                    )
            elif once:
                break
            else:
                time.sleep(0.5)
            if once:
                break
