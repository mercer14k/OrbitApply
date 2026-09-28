"""A serial background worker and thread-safe, session-owned folder queue.

Workers never read Streamlit state or call Streamlit APIs. UI reads snapshots.
"""

from concurrent.futures import CancelledError, Future
from copy import deepcopy
from queue import Queue
from threading import Lock, Thread
from uuid import uuid4

from . import workspace as workspace_actions
from .evidence_tailoring import COMPLETED
from .ollama_client import OllamaClient


class SerialExecutor:
    """One daemon worker per app process, shared by all browser sessions."""

    def __init__(self, name="job-folder-worker"):
        self._items = Queue()
        self._thread = Thread(target=self._work, name=name, daemon=True)
        self._thread.start()

    def submit(self, function, *args):
        future = Future()
        self._items.put((future, function, args))
        return future

    def _work(self):
        while True:
            item = self._items.get()
            if item is None:
                self._items.task_done()
                return
            future, function, args = item
            try:
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(function(*args))
                    except BaseException as exc:  # noqa: BLE001 - preserve future semantics and worker lifetime
                        future.set_exception(exc)
            finally:
                self._items.task_done()

    def close(self):
        self._items.put(None)


_executor = None
_executor_lock = Lock()
_notification_executor = None
_notification_executor_lock = Lock()


def shared_executor():
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = SerialExecutor()
        return _executor


def shared_notification_executor():
    global _notification_executor
    with _notification_executor_lock:
        if _notification_executor is None:
            _notification_executor = SerialExecutor(name="telegram-alert-worker")
        return _notification_executor


class FolderQueue:
    def __init__(
        self,
        workspace,
        *,
        executor=None,
        processor=None,
        client_factory=None,
        notifications=None,
        notification_executor=None,
    ):
        self._workspace = deepcopy(workspace)
        self._lock = Lock()
        self._jobs = {}
        self._futures = {}
        self._executor = executor or shared_executor()
        self._processor = processor
        self._client_factory = client_factory or OllamaClient
        self._notifications = notifications
        self._notification_executor = notification_executor
        self._notification_futures = []

    @property
    def notifying(self):
        with self._lock:
            return any(
                j.get("notification") == "Sending Telegram alert..."
                for j in self._jobs.values()
            )

    @property
    def busy(self):
        with self._lock:
            return any(j["state"] in {"queued", "running"} for j in self._jobs.values())

    def enqueue(self, listing_id, *, model, context_window):
        with self._lock:
            for task_id, item in self._jobs.items():
                if item["listing_id"] == listing_id and item["state"] in {
                    "queued",
                    "running",
                }:
                    return task_id  # Repeated clicks never submit a duplicate.
            row = next(
                r for r in self._workspace["roles"] if r["listing_id"] == listing_id
            )
            if not row["included"]:
                raise ValueError("This job is excluded by the search filter.")
            from pathlib import Path

            if (
                row["status"] in COMPLETED
                and row["folder"]
                and Path(row["folder"]).is_dir()
            ):
                return None
            task_id = uuid4().hex
            self._jobs[task_id] = {
                "id": task_id,
                "listing_id": listing_id,
                "role": row["role"],
                "company": row["company"],
                "state": "queued",
                "detail": "Waiting for the AI",
                "folder": row["folder"],
                "notification": "",
            }
            # Capture settings at enqueue time; the worker receives no UI objects.
            self._futures[task_id] = self._executor.submit(
                self._run, task_id, {"model": model, "context_window": context_window}
            )
            return task_id

    def cancel(self, task_id):
        with self._lock:
            item = self._jobs.get(task_id)
            if not item or item["state"] != "queued":
                return False
            if not self._futures[task_id].cancel():
                return (
                    False  # It has already started; never interrupt an active export.
                )
            item.update(state="cancelled", detail="Removed from the queue")
            return True

    def snapshot(self):
        with self._lock:
            jobs = deepcopy(list(self._jobs.values()))
            return {
                "jobs": jobs,
                "busy": any(j["state"] in {"queued", "running"} for j in jobs),
                "roles": deepcopy(self._workspace["roles"]),
                "folder": self._workspace["folder"],
                "notifying": any(
                    j.get("notification") == "Sending Telegram alert..." for j in jobs
                ),
            }

    def wait_idle(self, timeout=10):
        """Wait for currently submitted tasks; used by controlled tests, not the UI."""
        with self._lock:
            futures = list(self._futures.values())
        for future in futures:
            try:
                future.result(timeout=timeout)
            except CancelledError:
                pass

    def wait_notifications(self, timeout=10):
        """Controlled tests call this after wait_idle; it never blocks tailoring."""
        with self._lock:
            futures = list(self._notification_futures)
        for future in futures:
            future.result(timeout=timeout)

    def _send_notification(self, task_id, generation, row, completed):
        try:
            detail = self._notifications.send(generation, row, completed)
        except Exception:  # noqa: BLE001 - notification failure cannot change folder outcome
            detail = "Telegram delivery could not be confirmed. Check your bot and internet connection."
        with self._lock:
            self._jobs[task_id]["notification"] = detail

    def _progress(self, task_id, message):
        with self._lock:
            self._jobs[task_id]["detail"] = str(message)

    def _run(self, task_id, settings):
        with self._lock:
            item = self._jobs[task_id]
            item.update(state="running", detail="Reading the job description...")
            listing_id = item["listing_id"]
            working = deepcopy(self._workspace)
        row = next(r for r in working["roles"] if r["listing_id"] == listing_id)
        state, detail = "failed", "The job stopped before completion."
        try:
            client = self._client_factory(**settings)
            client.progress = lambda message: self._progress(task_id, message)
            processor = self._processor or workspace_actions.create_role_folder
            processor(working, listing_id, client)
            state = "done" if row["status"] in COMPLETED else "failed"
            detail = row.get("reason") or row["status"]
        except Exception as exc:  # noqa: BLE001 - isolate one failure from later selections
            state, detail = "failed", str(exc)
            row.update(status="Needs review", reason=detail)
        finally:
            generation = (
                self._notifications.capture()
                if self._notifications is not None
                else None
            )
            # Publish a complete workspace snapshot only after the worker finishes.
            with self._lock:
                self._workspace = working
                self._jobs[task_id].update(
                    state=state,
                    detail=detail,
                    folder=row["folder"],
                    notification="Sending Telegram alert..."
                    if generation is not None
                    else "",
                )
            # Credentials and messages are never added to the saved workspace.
            # Notification I/O uses a separate worker so the next AI job can start.
            if generation is not None:
                try:
                    executor = (
                        self._notification_executor or shared_notification_executor()
                    )
                    with self._lock:
                        self._notification_futures.append(
                            executor.submit(
                                self._send_notification,
                                task_id,
                                generation,
                                deepcopy(row),
                                state == "done",
                            )
                        )
                except Exception:  # noqa: BLE001 - retain completed folder even if scheduling fails
                    with self._lock:
                        self._jobs[task_id]["notification"] = (
                            "Telegram alert could not be scheduled."
                        )
