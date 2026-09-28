"""Cancellable discovery with isolated snapshots; no Streamlit calls in workers."""

from copy import deepcopy
from threading import Event, Lock, Thread
from uuid import uuid4

from . import workspace as workspace_actions


class SearchTask:
    def __init__(self, payload, *, discoverer=None):
        self.id = uuid4().hex
        self._payload = deepcopy(payload)
        self._discoverer = discoverer or workspace_actions.discover
        self._cancel = Event()
        self._lock = Lock()
        self._thread = None
        self._state = "pending"
        self._detail = "Starting search..."
        self._done = 0
        self._total = 0
        self._workspace = None
        self._error = ""

    @property
    def busy(self):
        with self._lock:
            return self._state in {"pending", "running"}

    def start(self):
        with self._lock:
            if self._state != "pending":
                return
            self._state = "running"
            self._thread = Thread(
                target=self._run, name="job-search-worker", daemon=True
            )
            self._thread.start()

    def cancel(self):
        with self._lock:
            if self._state not in {"pending", "running"}:
                return False
            self._cancel.set()
            self._state = "cancelled"
            self._detail = "Search cancelled. Results found so far are available below."
            if self._workspace is not None:
                self._workspace["status"] = "Search cancelled"
                self._workspace["warnings"].append(
                    "Search cancelled. Partial results are retained; unchecked sponsorship is not treated as an offer."
                )
            return True

    def snapshot(self):
        with self._lock:
            return {
                "id": self.id,
                "state": self._state,
                "busy": self._state in {"pending", "running"},
                "detail": self._detail,
                "done": self._done,
                "total": self._total,
                "workspace": deepcopy(self._workspace),
                "error": self._error,
            }

    def wait(self, timeout=10):
        """Controlled tests only. Cancel never waits for a website request."""
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise TimeoutError(
                    "The search worker is still finishing its active request."
                )

    def _progress(self, done, total, message):
        with self._lock:
            if self._state == "running":
                self._done, self._total, self._detail = done, total, str(message)

    def _partial(self, workspace):
        with self._lock:
            if self._state == "running":
                self._workspace = deepcopy(workspace)

    def _run(self):
        task = self._payload
        try:
            result = self._discoverer(
                task["prepared"],
                task["roles"],
                task["settings"],
                task["output"],
                task["sponsorship"],
                task["browser"],
                progress=self._progress,
                cancel_event=self._cancel,
                partial=self._partial,
                excluded_posting_keys=task.get("excluded_posting_keys", []),
            )
            with self._lock:
                if self._state == "running":
                    self._workspace = deepcopy(result)
                    self._state = "done"
                    self._detail = "Search complete"
        except Exception as exc:  # noqa: BLE001 - release UI and retain partial results
            with self._lock:
                if self._state == "running":
                    self._state = "failed"
                    self._error = str(exc)
                    self._detail = (
                        "Search stopped with an error. Partial results are retained."
                    )
                    if self._workspace is not None:
                        self._workspace["status"] = "Search failed; partial results"
