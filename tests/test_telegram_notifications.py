import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
from test_folder_queue import workspace_jobs

from jobtailor.evidence_tailoring import ALIGNED, READY
from jobtailor.folder_queue import FolderQueue, SerialExecutor
from jobtailor.telegram_notifications import (
    TelegramAPI,
    TelegramError,
    TelegramNotifications,
    TelegramPairing,
    TelegramTarget,
    begin_pairing,
    finish_pairing,
    folder_message,
)

TOKEN = "123456:" + "x" * 35
TARGET = TelegramTarget(TOKEN, 123, "Test user", "job_test_bot")


class TelegramTests(unittest.TestCase):
    def test_send_payload_and_no_credentials_in_public_state(self):
        notifications = TelegramNotifications()
        self.assertIsNone(notifications.capture())
        notifications.connect(TARGET)
        with tempfile.TemporaryDirectory() as folder:
            row = {
                "role": "Engineer",
                "company": "Test Co",
                "folder": folder,
                "status": READY,
                "url": "https://example.com/apply",
                "resume": "PRIVATE RESUME",
            }
            with patch(
                "urllib.request.urlopen",
                return_value=io.BytesIO(b'{"ok":true,"result":{"message_id":1}}'),
            ) as request:
                notifications.send(notifications.capture(), row, True)
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["chat_id"], 123)
            self.assertIn("Folder created\nRole: Engineer", payload["text"])
            self.assertIn(folder, payload["text"])
            self.assertIn("https://example.com/apply", payload["text"])
            self.assertNotIn("PRIVATE RESUME", payload["text"])
            self.assertNotIn("parse_mode", payload)
            self.assertEqual(request.call_args.kwargs["timeout"], 10)
            self.assertNotIn(TOKEN, repr(notifications.snapshot()) + repr(TARGET))

    def test_pause_and_disconnect_invalidate_waiting_deliveries(self):
        notifications = TelegramNotifications()
        notifications.connect(TARGET)
        delivery = notifications.capture()
        notifications.set_enabled(False)
        notifications.set_enabled(True)
        with patch.object(TelegramAPI, "call") as request:
            self.assertIn("Skipped", notifications.send(delivery, {}, True))
            delivery = notifications.capture()
            notifications.disconnect()
            notifications.connect(TelegramTarget(TOKEN, 999, "Another", "job_test_bot"))
            self.assertIn("Skipped", notifications.send(delivery, {}, True))
            request.assert_not_called()

    def test_pairing_matches_only_private_chat_with_session_code(self):
        pairing = TelegramPairing(TOKEN, "job_test_bot", code="unique_code")

        def update(chat_id, text, kind="private", update_id=1):
            return {
                "update_id": update_id,
                "message": {
                    "text": text,
                    "chat": {"id": chat_id, "type": kind, "first_name": "Test user"},
                    "from": {"id": chat_id, "is_bot": False},
                },
            }

        updates = [
            update(999, "/start wrong"),
            update(-333, "/start unique_code", "group", 2),
            update(123, "/start unique_code", update_id=3),
        ]
        with patch.object(TelegramAPI, "call", return_value=updates):
            self.assertEqual(finish_pairing(pairing), TARGET)
        self.assertEqual(pairing.offset, 4)
        self.assertNotIn(TOKEN, pairing.url)

    def test_pairing_missing_expired_and_webhook_conflict(self):
        pairing = TelegramPairing(TOKEN, "job_test_bot")
        with patch.object(TelegramAPI, "call", return_value=[]):
            self.assertIsNone(finish_pairing(pairing))
        pairing.expires = 0
        with self.assertRaisesRegex(TelegramError, "expired"):
            finish_pairing(pairing)
        with patch.object(
            TelegramAPI,
            "call",
            side_effect=[
                {"is_bot": True, "username": "job_test_bot"},
                {"url": "https://example.com/hook"},
            ],
        ) as call:
            with self.assertRaisesRegex(TelegramError, "another service"):
                begin_pairing(TOKEN)
            self.assertEqual(
                [c.args[0] for c in call.call_args_list], ["getMe", "getWebhookInfo"]
            )

    def test_http_errors_and_timeouts_do_not_expose_token_or_retry(self):
        for error in [
            urllib.error.HTTPError(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage", 401, TOKEN, {}, None
            ),
            TimeoutError(TOKEN),
        ]:
            with (
                self.subTest(error=type(error).__name__),
                patch("urllib.request.urlopen", side_effect=error) as request,
            ):
                with self.assertRaises(TelegramError) as raised:
                    TelegramAPI(TOKEN).call("sendMessage")
                self.assertNotIn(TOKEN, str(raised.exception))
                request.assert_called_once()

    def test_api_rejection_and_malformed_json_are_not_success(self):
        for raw in [b'{"ok":false,"description":"private text"}', b"not json", b"null"]:
            with (
                self.subTest(raw=raw),
                patch("urllib.request.urlopen", return_value=io.BytesIO(raw)),
                self.assertRaises(TelegramError),
            ):
                TelegramAPI(TOKEN).call("sendMessage")

    def test_notification_preserves_review_aligned_and_failed_statuses(self):
        with tempfile.TemporaryDirectory() as folder:
            row = {"folder": folder, "status": "Needs review", "role": "Analyst"}
            self.assertTrue(
                folder_message(row, False).startswith("Folder created; review needed")
            )
            row["status"] = ALIGNED
            self.assertIn(ALIGNED, folder_message(row, True))
            self.assertNotIn("tailored", folder_message(row, True).lower())
        self.assertTrue(
            folder_message(row, False).startswith("Folder could not be completed")
        )
        self.assertNotIn("Folder on your computer", folder_message(row, True))


class NotificationQueueTests(unittest.TestCase):
    def setUp(self):
        self.worker, self.alert_worker = SerialExecutor(), SerialExecutor("test-alerts")
        self.addCleanup(self.worker.close)
        self.addCleanup(self.alert_worker.close)

    def make_queue(self, processor, notifier):
        return FolderQueue(
            workspace_jobs(),
            executor=self.worker,
            notification_executor=self.alert_worker,
            processor=processor,
            client_factory=Mock(),
            notifications=notifier,
        )

    def add(self, queue, job):
        return queue.enqueue(job, model="test", context_window=8192)

    def test_slow_telegram_does_not_delay_next_ai_job_and_sends_once(self):
        release, entered = Event(), Event()
        self.addCleanup(release.set)
        notifier = Mock()
        notifier.capture.return_value = 1

        def send(*args):
            entered.set()
            release.wait(5)
            return "Telegram alert sent"

        notifier.send.side_effect = send
        processed = []

        def process(workspace, listing_id, client):
            processed.append(listing_id)
            next(r for r in workspace["roles"] if r["listing_id"] == listing_id)[
                "status"
            ] = READY

        queue = self.make_queue(process, notifier)
        self.add(queue, "0")
        self.assertTrue(entered.wait(2))
        self.add(queue, "1")
        queue.wait_idle()
        self.assertEqual(processed, ["0", "1"])
        self.assertFalse(queue.busy)
        self.assertTrue(queue.notifying)
        self.assertEqual(
            [j["state"] for j in queue.snapshot()["jobs"]], ["done", "done"]
        )
        release.set()
        queue.wait_notifications()
        self.assertEqual(notifier.send.call_count, 2)
        self.assertFalse(queue.notifying)

    def test_cancelled_job_sends_nothing_and_delivery_failure_preserves_folder_result(
        self,
    ):
        release, entered = Event(), Event()
        self.addCleanup(release.set)
        notifier = Mock()
        notifier.capture.return_value = 1
        notifier.send.side_effect = RuntimeError(TOKEN)

        def process(workspace, listing_id, client):
            entered.set()
            release.wait(5)
            workspace["roles"][0]["status"] = READY

        queue = self.make_queue(process, notifier)
        first = self.add(queue, "0")
        self.assertTrue(entered.wait(2))
        self.assertEqual(first, self.add(queue, "0"))
        second = self.add(queue, "1")
        self.assertTrue(queue.cancel(second))
        release.set()
        queue.wait_idle()
        queue.wait_notifications()
        notifier.send.assert_called_once()
        snapshot = queue.snapshot()
        self.assertEqual([j["state"] for j in snapshot["jobs"]], ["done", "cancelled"])
        self.assertEqual(snapshot["roles"][0]["status"], READY)
        self.assertIn("could not be confirmed", snapshot["jobs"][0]["notification"])
        self.assertNotIn(TOKEN, repr(snapshot))

    def test_failed_job_notifies_failure_and_next_job_runs(self):
        notifier = Mock()
        notifier.capture.return_value = 1
        notifier.send.return_value = "Telegram alert sent"

        def process(workspace, listing_id, client):
            if listing_id == "0":
                raise OSError("Disk unavailable")
            workspace["roles"][1]["status"] = READY

        queue = self.make_queue(process, notifier)
        self.add(queue, "0")
        self.add(queue, "1")
        queue.wait_idle()
        queue.wait_notifications()
        self.assertEqual(
            [c.args[2] for c in notifier.send.call_args_list], [False, True]
        )


class TelegramUITests(unittest.TestCase):
    def test_connect_toggle_disconnect_survive_reruns(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with (
            patch(
                "jobtailor.telegram_ui.begin_pairing",
                return_value=TelegramPairing(TOKEN, "job_test_bot"),
            ),
            patch("jobtailor.telegram_ui.finish_pairing", return_value=TARGET),
            patch.object(TelegramAPI, "call") as api,
        ):
            app = AppTest.from_file(str(app_path), default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertTrue(app.button(key="telegram_connect_v012").disabled)
            app.text_input(key="telegram_token_v012").set_value(TOKEN).run()
            app.button(key="telegram_connect_v012").click().run()
            app.button(key="telegram_check_v012").click().run()
            self.assertFalse(app.exception, [e.message for e in app.exception])
            notifications = app.session_state.telegram_notifications_v012
            self.assertTrue(notifications.snapshot()["enabled"])
            app.run()
            self.assertTrue(notifications.snapshot()["enabled"])
            app.toggle(key="telegram_enabled_v012").set_value(False).run()
            self.assertFalse(notifications.snapshot()["enabled"])
            app.button(key="telegram_disconnect_v012").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(notifications.snapshot()["connected"])
            self.assertEqual(app.text_input(key="telegram_token_v012").value, "")
            api.assert_not_called()  # Setup never sends an unsolicited test message.
