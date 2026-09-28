import unittest
from copy import deepcopy
from threading import Event, Lock
from unittest.mock import Mock

from test_selection_ui import workspace_fixture

from jobtailor.evidence_tailoring import READY
from jobtailor.folder_queue import FolderQueue, SerialExecutor


def workspace_jobs(count=3):
    result = workspace_fixture("unused-test-output")
    original_row, original_job = result["roles"][0], result["jobs"][0]
    result["roles"], result["jobs"] = [], []
    for i in range(count):
        result["roles"].append(
            {**original_row, "listing_id": str(i), "role": f"Analyst {i}"}
        )
        result["jobs"].append({**original_job, "listing_id": str(i)})
    return result


class FolderQueueTests(unittest.TestCase):
    def setUp(self):
        self.executor = SerialExecutor()
        self.release = Event()
        self.entered = Event()
        self.addCleanup(self.executor.close)
        self.addCleanup(self.release.set)

    def make_queue(self, processor, workspace=None):
        return FolderQueue(
            workspace or workspace_jobs(),
            executor=self.executor,
            processor=processor,
            client_factory=Mock(),
        )

    def add(self, queue, listing_id):
        return queue.enqueue(listing_id, model="test-model", context_window=8192)

    def test_fifo_cancel_and_duplicate_clicks_while_one_job_is_running(self):
        order = []
        active, maximum = 0, 0
        lock = Lock()

        def process(workspace, listing_id, client):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            order.append(listing_id)
            if listing_id == "0":
                self.entered.set()
                self.release.wait(timeout=3)
            client.progress("Checking facts")
            next(r for r in workspace["roles"] if r["listing_id"] == listing_id)[
                "status"
            ] = READY
            with lock:
                active -= 1

        queue = self.make_queue(process)
        first = self.add(queue, "0")
        self.assertTrue(self.entered.wait(timeout=2))
        second = self.add(queue, "1")
        third = self.add(queue, "2")
        self.assertEqual(self.add(queue, "2"), third)
        self.assertFalse(queue.cancel(first))
        self.assertTrue(queue.cancel(second))
        self.assertEqual(
            [j["state"] for j in queue.snapshot()["jobs"]],
            ["running", "cancelled", "queued"],
        )
        self.release.set()
        queue.wait_idle()
        self.assertEqual(order, ["0", "2"])
        self.assertEqual(maximum, 1)
        self.assertFalse(queue.busy)
        self.assertEqual(queue.snapshot()["roles"][1]["folder"], "")
        self.assertEqual(queue.snapshot()["roles"][1]["status"], "Not prepared")

    def test_failure_does_not_stop_next_job(self):
        order = []

        def process(workspace, listing_id, client):
            order.append(listing_id)
            if listing_id == "0":
                raise OSError("Cannot write this folder")
            workspace["roles"][1]["status"] = READY

        queue = self.make_queue(process)
        self.add(queue, "0")
        self.add(queue, "1")
        queue.wait_idle()
        self.assertEqual(order, ["0", "1"])
        snapshot = queue.snapshot()
        self.assertEqual([j["state"] for j in snapshot["jobs"]], ["failed", "done"])
        self.assertIn("Cannot write", snapshot["roles"][0]["reason"])
        self.assertFalse(queue.busy)

    def test_cancelled_job_can_be_requeued_at_the_end(self):
        order = []

        def process(workspace, listing_id, client):
            order.append(listing_id)
            if listing_id == "0":
                self.entered.set()
                self.release.wait(timeout=3)
            next(r for r in workspace["roles"] if r["listing_id"] == listing_id)[
                "status"
            ] = READY

        queue = self.make_queue(process)
        self.add(queue, "0")
        self.assertTrue(self.entered.wait(timeout=2))
        second = self.add(queue, "1")
        self.add(queue, "2")
        self.assertTrue(queue.cancel(second))
        self.assertNotEqual(second, self.add(queue, "1"))
        self.release.set()
        queue.wait_idle()
        self.assertEqual(order, ["0", "2", "1"])

    def test_background_worker_uses_copies_and_captured_model_settings(self):
        original = workspace_jobs()
        seen = []

        def process(workspace, listing_id, client):
            seen.append(deepcopy(workspace))
            workspace["folder"] = "worker-created-root"
            workspace["roles"][0]["status"] = READY

        factory = Mock()
        queue = FolderQueue(
            original, executor=self.executor, processor=process, client_factory=factory
        )
        original["output_root"] = "changed-after-queue-created"
        self.add(queue, "0")
        queue.wait_idle()
        self.assertEqual(seen[0]["output_root"], "unused-test-output")
        self.assertEqual(original["folder"], "")
        factory.assert_called_once_with(model="test-model", context_window=8192)
        snapshot = queue.snapshot()
        snapshot["roles"][0]["status"] = "tampered"
        self.assertEqual(queue.snapshot()["roles"][0]["status"], READY)

    def test_sessions_share_one_worker_and_can_cancel_while_waiting(self):
        ran_second = Mock()

        def first_job(*args):
            self.entered.set()
            self.release.wait(timeout=3)

        first_queue = self.make_queue(first_job)
        second_queue = self.make_queue(ran_second)
        self.add(first_queue, "0")
        self.assertTrue(self.entered.wait(timeout=2))
        second = self.add(second_queue, "1")
        self.assertEqual(second_queue.snapshot()["jobs"][0]["state"], "queued")
        self.assertTrue(second_queue.cancel(second))
        self.release.set()
        first_queue.wait_idle()
        second_queue.wait_idle()
        ran_second.assert_not_called()
