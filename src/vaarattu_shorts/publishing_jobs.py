"""Observable, stoppable queue fills. Submission receipts remain the source of truth."""

import json
import threading
import time
import uuid

from .processes import LockBusyError, lock
from .storage import atomic_json


class FillStopped(ValueError):
    pass


class FillJob:
    def __init__(self, settings):
        self.path = settings.work / "publishing-fill.json"
        self.publishing_lock = settings.work / "buffer-publishing.lock"
        self.mutex = threading.RLock()
        self.active = False
        self.cancel = threading.Event()

    def _read(self):
        return json.loads(self.path.read_text("utf-8")) if self.path.exists() else {"state": "idle"}

    def status(self):
        with self.mutex:
            state = self._read()
            if state["state"] == "running" and not self.active:
                try:
                    with lock(self.publishing_lock):
                        state.update(state="interrupted", message="Queue filling was interrupted. Saved copy and submission receipts are kept. Retry to fill remaining slots.")
                        atomic_json(self.path, state)
                except LockBusyError:
                    pass
            return state

    def stop(self, job_id):
        with self.mutex:
            state = self._read()
            if state.get("id") != job_id or state["state"] != "running" or not self.active:
                raise ValueError("This fill is no longer running. Refresh its status.")
            self.cancel.set()
            self.progress("Stopping… Waiting for the current upload or submission to finish. Accepted posts stay scheduled.", stopping=True)
            return self._read()

    def progress(self, message, **fields):
        with self.mutex:
            state = self._read()
            # Keep the stop acknowledgement visible while an in-flight submission finishes.
            if not state.get("stopping") or fields.get("stopping") or fields.get("state"):
                state["message"] = message
            state.update(fields)
            atomic_json(self.path, state)

    def run(self, function, analysis):
        # Caller holds the shared publishing lock before entering this method.
        started = time.monotonic()
        with self.mutex:
            self.active = True
            self.cancel.clear()
            atomic_json(self.path, {"id": uuid.uuid4().hex, "state": "running", "stage": "checking",
                                   "message": "Starting queue fill…", "started_at": time.time(),
                                   "provider": analysis["provider"], "scheduled": 0})

        def check():
            if self.cancel.is_set():
                raise FillStopped("Queue fill stopped. Saved copy and accepted posts are kept. Retry to fill remaining slots.")
            if time.monotonic() - started >= 1800:
                raise ValueError("Queue fill reached its 30-minute limit. Saved copy and accepted posts are kept. Retry to fill remaining slots.")

        try:
            result = function(analysis=analysis, progress=self.progress, check=check)
            self.progress(result.get("message") or "Queue fill completed.", state="completed", result=result)
            return result
        except Exception as exc:
            self.progress(str(exc), state="stopped" if isinstance(exc, FillStopped) else "failed", stopping=False)
            raise
        finally:
            with self.mutex:
                self.active = False
