"""Real Streamlit callbacks: suggestions, busy rendering, and per-row actions."""

import io
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from threading import Event
from unittest.mock import patch

import streamlit as st
from streamlit.proto.WidgetStates_pb2 import WidgetStates
from streamlit.runtime.uploaded_file_manager import UploadedFileRec
from streamlit.testing.v1 import AppTest
from test_core import fixture_job, fixture_profile

APP = Path(__file__).resolve().parents[1] / "app.py"


def prepared_fixture():
    return {
        "profile": fixture_profile().model_dump(),
        "source": "Resume fixture",
        "keywords": [{"keyword": "SQL", "category": "Technology"}],
        "role_titles": ["Supply Chain Analyst", "Inventory Analyst"],
        "warnings": [],
        "coverage": {},
    }


def workspace_fixture(output):
    job = fixture_job()
    return {
        "folder": "",
        "output_root": output,
        "prepared": prepared_fixture(),
        "jobs": [job.model_dump()],
        "selected_roles": ["Inventory Analyst"],
        "keywords": ["Inventory Analyst", "SQL"],
        "queries": ["Inventory Analyst"],
        "warnings": [],
        "roles": [
            {
                "listing_id": job.listing_id,
                "company": job.company,
                "role": job.title,
                "url": job.job_url,
                "status": "Not prepared",
                "folder": "",
                "included": True,
            }
        ],
    }


class InteractiveUITests(unittest.TestCase):
    def setup_app(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = temporary.name
        stack.enter_context(
            patch.dict(
                os.environ, {"JOBTAILOR_DATA_DIR": str(Path(self.output) / "history")}
            )
        )
        upload = io.BytesIO(b"resume fixture")
        upload.name = "resume.txt"
        stack.enter_context(patch("streamlit.file_uploader", return_value=upload))
        self.analyze = stack.enter_context(
            patch("jobtailor.workspace.prepare_resume", return_value=prepared_fixture())
        )
        self.search = stack.enter_context(
            patch(
                "jobtailor.workspace.discover",
                return_value=workspace_fixture(self.output),
            )
        )
        self.make_folder = stack.enter_context(
            patch("jobtailor.workspace.create_role_folder")
        )
        self.open_folder = stack.enter_context(patch("jobtailor.desktop.open_folder"))
        self.start_renders = []
        self.start_callback = None
        self.replay_start = False
        original_button = st.button

        def tracked_button(label, *args, **kwargs):
            if label == "Start":
                self.start_renders.append(kwargs.get("disabled", False))
                self.start_callback = kwargs.get("on_click")
                if self.replay_start:
                    self.replay_start = False
                    self.start_callback()
            return original_button(label, *args, **kwargs)

        stack.enter_context(patch("streamlit.button", side_effect=tracked_button))
        app = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        return app

    def test_resume_suggestions_are_cached_and_selected_before_search(self):
        app = self.setup_app()
        self.analyze.assert_called_once()
        self.assertEqual(
            app.multiselect(key="roles_to_search_v09").value,
            prepared_fixture()["role_titles"],
        )
        app.multiselect(key="roles_to_search_v09").unselect(
            "Supply Chain Analyst"
        ).run()
        self.analyze.assert_called_once()
        self.search.assert_not_called()
        app.button(key="start_v09").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(self.search.call_args.args[1], ["Inventory Analyst"])
        self.make_folder.assert_not_called()
        self.assertEqual(list(Path(self.output).iterdir()), [])

    def test_ai_usage_is_visible_without_private_prompt_text(self):
        from jobtailor.content_compression import AgentContentCompressor
        app = self.setup_app()
        workspace = workspace_fixture(self.output)
        ledger = AgentContentCompressor()
        ledger.record_response({"prompt_eval_count": 81, "eval_count": 12}, .5)
        workspace["roles"][0]["ai_usage"] = ledger.snapshot()
        app.session_state.job_workspace_v09 = workspace
        app.run()
        self.assertFalse(app.exception)
        self.assertIn("AI usage", [x.label for x in app.expander])
        frames = [f.value for f in app.dataframe if "Input tokens (reported)" in f.value]
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].iloc[0]["Input tokens (reported)"], 81)
        self.assertEqual(frames[0].iloc[0]["Input usage coverage"], "1/1 responses")

    def test_start_is_disabled_before_work_and_duplicate_callbacks_are_ignored(self):
        app = self.setup_app()
        entered, release = Event(), Event()

        def searching(*args, **kwargs):
            self.assertTrue(self.start_renders[-1])
            entered.set()
            release.wait(5)
            return workspace_fixture(self.output)

        self.search.side_effect = searching
        try:
            app.button(key="start_v09").click().run()
            self.assertTrue(entered.wait(2))
            task = app.session_state.search_task_v013
            self.assertTrue(task.busy)
            self.assertTrue(app.button(key="start_v09").disabled)
            self.assertFalse(app.button(key="cancel_search_v013").disabled)
            # Replay a queued callback in the UI thread while the task is busy.
            self.replay_start = True
            app.run()
            self.assertIs(app.session_state.search_task_v013, task)
            self.search.assert_called_once()
        finally:
            release.set()
            app.session_state.search_task_v013.wait()
        app.run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertFalse(app.button(key="start_v09").disabled)

    def test_cancel_search_keeps_partial_results_and_late_worker_cannot_replace_new_search(
        self,
    ):
        app = self.setup_app()
        entered, release = Event(), Event()

        def searching(*args, **kwargs):
            kwargs["partial"](workspace_fixture(self.output))
            entered.set()
            release.wait(5)
            late = workspace_fixture(self.output)
            late["roles"][0]["company"] = "Old late response"
            kwargs["partial"](late)
            return late

        self.search.side_effect = searching
        old = None
        try:
            app.button(key="start_v09").click().run()
            self.assertTrue(entered.wait(2))
            old = app.session_state.search_task_v013
            app.button(key="cancel_search_v013").click().run()
            self.assertFalse(app.exception, [e.message for e in app.exception])
            self.assertFalse(app.button(key="start_v09").disabled)
            self.assertEqual(old.snapshot()["state"], "cancelled")
            self.assertEqual(len(app.session_state.job_workspace_v09["roles"]), 1)
            self.assertFalse(
                next(b for b in app.button if b.label == "Create folder").disabled
            )
            self.make_folder.assert_not_called()
            fresh = workspace_fixture(self.output)
            fresh["roles"][0]["company"] = "Fresh search"
            self.search.side_effect = None
            self.search.return_value = fresh
            app.button(key="start_v09").click().run()
            app.session_state.search_task_v013.wait()
            app.run()
        finally:
            release.set()
            if old:
                old.wait()
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(
            app.session_state.job_workspace_v09["roles"][0]["company"], "Fresh search"
        )
        self.assertEqual(self.search.call_count, 2)

    def test_start_recovers_after_search_failure(self):
        app = self.setup_app()
        self.search.side_effect = RuntimeError("Search service unavailable")
        app.button(key="start_v09").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.button(key="start_v09").disabled)
        self.assertTrue(
            any("Search service unavailable" in error.value for error in app.error)
        )

    def test_per_row_create_changes_to_open_folder(self):
        app = self.setup_app()
        app.button(key="start_v09").click().run()
        row = app.session_state.job_workspace_v09["roles"][0]

        def prepare(workspace, listing_id, client):
            self.assertEqual(listing_id, row["listing_id"])
            folder = Path(self.output) / "Requested_role"
            folder.mkdir()
            workspace["roles"][0].update(
                folder=str(folder), status="Draft ready; review before applying"
            )

        self.make_folder.side_effect = prepare
        next(b for b in app.button if b.label == "Create folder").click().run()
        app.session_state.folder_queue_v011.wait_idle()
        app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.make_folder.assert_called_once()
        next(b for b in app.button if b.label == "Open folder").click().run()
        self.open_folder.assert_called_once_with(
            str(Path(self.output) / "Requested_role")
        )
        self.make_folder.assert_called_once()

    def test_controls_recover_after_folder_failure(self):
        app = self.setup_app()
        app.button(key="start_v09").click().run()
        self.make_folder.side_effect = OSError("Destination is unavailable")
        next(b for b in app.button if b.label == "Create folder").click().run()
        app.session_state.folder_queue_v011.wait_idle()
        app.run()
        self.assertFalse(app.exception)
        self.assertFalse(app.button(key="start_v09").disabled)
        self.assertTrue(
            any(
                "Destination is unavailable" in e.value
                for e in [*app.caption, *app.markdown]
            )
        )

    def test_live_worker_allows_queue_cancel_pagination_and_open_folder(self):
        from test_folder_queue import workspace_jobs

        app = self.setup_app()
        workspace = workspace_jobs(25)
        workspace["output_root"] = self.output
        existing = Path(self.output) / "Already_prepared"
        existing.mkdir()
        workspace["roles"][4].update(
            folder=str(existing), status="Draft ready; review before applying"
        )
        self.search.return_value = workspace
        app.button(key="start_v09").click().run()
        entered, release = Event(), Event()
        processed = []

        def prepare(working, listing_id, client):
            processed.append(listing_id)
            if listing_id == "0":
                entered.set()
                release.wait(timeout=10)
            row = next(r for r in working["roles"] if r["listing_id"] == listing_id)
            folder = Path(self.output) / f"Role_{listing_id}"
            folder.mkdir()
            row.update(folder=str(folder), status="Draft ready; review before applying")

        self.make_folder.side_effect = prepare
        try:
            app.button(key="create_1_0").click().run()
            self.assertTrue(entered.wait(timeout=2))
            self.assertFalse(app.exception)
            self.assertTrue(app.button(key="start_v09").disabled)
            self.assertFalse(app.button(key="create_1_1").disabled)
            self.assertFalse(app.button(key="open_1_4").disabled)
            app.button(key="open_1_4").click().run()
            self.open_folder.assert_called_once_with(str(existing))
            app.button(key="create_1_1").click().run()
            app.button(key="create_1_2").click().run()
            queue = app.session_state.folder_queue_v011
            cancelled = next(
                j for j in queue.snapshot()["jobs"] if j["listing_id"] == "1"
            )
            app.button(key=f"cancel_{cancelled['id']}").click().run()
            self.assertFalse(app.button(key="create_1_1").disabled)
            self.assertFalse(app.number_input(key="result_page_v09").disabled)
            app.number_input(key="result_page_v09").set_value(2).run()
            self.assertFalse(app.button(key="create_1_24").disabled)
            app.button(key="create_1_24").click().run()
            self.assertEqual(processed, ["0"])
            self.assertEqual(
                [
                    j["listing_id"]
                    for j in queue.snapshot()["jobs"]
                    if j["state"] == "queued"
                ],
                ["2", "24"],
            )
            self.assertTrue(any(h.value == "Folder queue" for h in app.subheader))
        finally:
            release.set()
            app.session_state.folder_queue_v011.wait_idle()
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(processed, ["0", "2", "24"])
        self.assertFalse(app.button(key="start_v09").disabled)
        self.assertFalse((Path(self.output) / "Role_1").exists())


class ResumeUploadLifecycleTests(unittest.TestCase):
    """Exercise the real uploader serialization and reruns, not a widget mock."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.records = {}
        self.stack.enter_context(
            patch(
                "streamlit.runtime.memory_uploaded_file_manager.MemoryUploadedFileManager.get_files",
                side_effect=lambda session_id, file_ids: [
                    self.records[file_id]
                    for file_id in file_ids
                    if file_id in self.records
                ],
            )
        )
        self.analyze = self.stack.enter_context(
            patch("jobtailor.workspace.prepare_resume", return_value=prepared_fixture())
        )
        self.app = AppTest.from_file(str(APP), default_timeout=20).run()

    def upload(self, file_id=None, name="resume.txt", data=b"resume fixture"):
        state = WidgetStates()
        widget = state.widgets.add()
        widget.id = self.app.get("file_uploader")[0].proto.id
        widget.file_uploader_state_value.SetInParent()
        if file_id:
            self.records[file_id] = UploadedFileRec(file_id, name, "text/plain", data)
            info = widget.file_uploader_state_value.uploaded_file_info.add()
            info.file_id, info.name, info.size = file_id, name, len(data)
        self.app._run(state)
        self.assertFalse(self.app.exception, [e.message for e in self.app.exception])

    def test_upload_survives_analysis_and_rerun(self):
        self.upload("first")
        self.analyze.assert_called_once()
        self.assertIsNotNone(self.app.get("file_uploader")[0].value)
        self.assertEqual(
            self.app.multiselect(key="roles_to_search_v09").value,
            prepared_fixture()["role_titles"],
        )
        self.assertFalse(self.app.button(key="start_v09").disabled)
        self.app.run()
        self.assertFalse(self.app.exception)
        self.analyze.assert_called_once()
        self.assertIsNotNone(self.app.session_state.prepared_resume_v09)

    def test_replacement_uses_new_resume_and_clears_old_search(self):
        self.upload("first")
        self.app.session_state.job_workspace_v09 = workspace_fixture("unused")
        new_profile = prepared_fixture()
        new_profile["role_titles"] = ["Procurement Analyst"]
        self.analyze.return_value = new_profile
        self.upload("second", "replacement.txt", b"different resume")
        self.assertEqual(self.analyze.call_count, 2)
        self.assertEqual(
            self.analyze.call_args.args[:2], ("replacement.txt", b"different resume")
        )
        self.assertEqual(
            self.app.multiselect(key="roles_to_search_v09").value,
            ["Procurement Analyst"],
        )
        self.assertIsNone(self.app.session_state.job_workspace_v09)

    def test_removal_clears_profile_and_disables_start(self):
        self.upload("first")
        self.upload()
        self.assertIsNone(self.app.session_state.prepared_resume_v09)
        self.assertIsNone(self.app.session_state.resume_upload_snapshot_v091)
        self.assertEqual(self.app.multiselect(key="roles_to_search_v09").value, [])
        self.assertTrue(self.app.button(key="start_v09").disabled)

    def test_failed_analysis_keeps_upload_for_retry(self):
        self.analyze.side_effect = RuntimeError("Ollama unavailable")
        self.upload("first")
        self.assertFalse(self.app.session_state.worker_busy)
        self.assertTrue(self.app.button(key="start_v09").disabled)
        self.assertTrue(
            any("Ollama unavailable" in error.value for error in self.app.error)
        )
        self.analyze.side_effect = None
        next(
            b for b in self.app.button if b.label == "Retry resume analysis"
        ).click().run()
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.button(key="start_v09").disabled)

    def test_stale_reset_flag_with_no_profile_is_safe(self):
        self.app.session_state.reset_role_choices_v09 = True
        self.app.session_state.prepared_resume_v09 = None
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertTrue(self.app.button(key="start_v09").disabled)


if __name__ == "__main__":
    unittest.main()
