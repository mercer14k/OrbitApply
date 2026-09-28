"""Keep the landing shell independent of local inference and portal inputs."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from jobtailor import site_shell
from test_selection_ui import prepared_fixture

ROOT = Path(__file__).resolve().parents[1]


class SiteShellTests(unittest.TestCase):
    def setUp(self):
        # AppTest creates a fresh component registry for each synthetic runtime.
        site_shell.landing_component.clear()

    def test_overview_is_initial_and_does_not_call_ai(self):
        with patch("jobtailor.ollama_client.OllamaClient._request") as request:
            app = AppTest.from_file(str(ROOT / "launch.py"), default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertEqual([e.label for e in app.get("page_link")][:4], ["Overview", "How it works", "Privacy", "Portal ↗"])
            self.assertFalse(app.get("file_uploader"))
            request.assert_not_called()

    @staticmethod
    def follow_link(app, label):
        # AppTest's public switch_page supports files only; use the actual
        # registered page hash emitted by st.page_link for callable pages.
        link = next(e for e in app.get("page_link") if e.label == label)
        app._page_hash = link.proto.page_script_hash
        return app.run()

    def test_navigation_preserves_resume_and_model_settings(self):
        app = AppTest.from_file(str(ROOT / "launch.py"), default_timeout=20).run()
        app.session_state["resume_upload_snapshot_v091"] = {"name":"resume.txt", "data":b"example"}
        app.session_state["upload_fingerprint_v09"] = hashlib.sha256(b"resume.txtexample").hexdigest()
        app.session_state["prepared_resume_v09"] = prepared_fixture()
        app.session_state["roles_to_search_v09"] = ["Inventory Analyst"]
        app.switch_page("app.py").run()
        self.assertFalse(app.exception)
        app.text_input(key="model_v09").set_value("custom-local-model").run()
        app.text_input(key="location_v09").set_value("Chicago").run()
        self.follow_link(app, "Overview")
        self.assertFalse(app.exception)
        self.assertFalse(app.get("file_uploader"))
        self.follow_link(app, "How it works")
        self.assertFalse(app.exception)
        self.follow_link(app, "Privacy")
        self.assertFalse(app.exception)
        app.switch_page("app.py").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key="model_v09").value, "custom-local-model")
        self.assertEqual(app.text_input(key="location_v09").value, "Chicago")
        self.assertEqual(app.multiselect(key="roles_to_search_v09").value, ["Inventory Analyst"])
        self.assertEqual(app.session_state["resume_upload_snapshot_v091"]["data"], b"example")

    def test_busy_guard_includes_search_and_folder_work(self):
        for key in ("worker_busy", "search_task_v013", "folder_queue_v011"):
            value = True if key == "worker_busy" else SimpleNamespace(busy=True)
            with patch.object(site_shell.st, "session_state", {key:value}):
                self.assertTrue(site_shell.portal_busy())
        with patch.object(site_shell.st, "session_state", {}):
            self.assertFalse(site_shell.portal_busy())

    def test_graphics_have_no_network_dependency_and_are_bundled(self):
        html = (ROOT / "jobtailor/assets/landing.html").read_text()
        source = (ROOT / "jobtailor/assets/landing.js").read_text()
        bundle = (ROOT / "jobtailor/assets/landing.bundle.js").read_text()
        self.assertIn("Illustrative preview", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("fetch(", source)
        self.assertIn("from 'three'", source)
        self.assertGreater(len(bundle), 100000)
        self.assertTrue((ROOT / "jobtailor/assets/THREE-LICENSE.txt").exists())
        for contract in ("prefers-reduced-motion", "IntersectionObserver", "visibilitychange", "1000 / 24", "forceContextLoss", "return cleanup"):
            self.assertIn(contract, source)

    def test_both_launchers_start_the_overview(self):
        for name in ("run_windows.bat", "run_macos.command"):
            content = (ROOT / name).read_text()
            self.assertIn("streamlit run launch.py", content)
            self.assertIn("127.0.0.1", content)


if __name__ == "__main__":
    unittest.main()
