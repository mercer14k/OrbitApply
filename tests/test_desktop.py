import subprocess
import unittest
from unittest.mock import patch

from jobtailor.desktop import choose_folder, open_folder


class MacDesktopTests(unittest.TestCase):
    @patch("jobtailor.desktop.platform.system", return_value="Darwin")
    @patch("jobtailor.desktop.subprocess.run")
    def test_native_picker_preserves_spaces(self, run, _platform):
        run.return_value = subprocess.CompletedProcess(
            [], 0, "/Users/Test/Job Applications/\n", ""
        )
        self.assertEqual(choose_folder(), "/Users/Test/Job Applications/")
        self.assertEqual(run.call_args.args[0][0], "osascript")

    @patch("jobtailor.desktop.platform.system", return_value="Darwin")
    @patch("jobtailor.desktop.subprocess.run")
    def test_cancel_does_not_become_error(self, run, _platform):
        run.return_value = subprocess.CompletedProcess(
            [], 1, "", "User canceled. (-128)"
        )
        self.assertEqual(choose_folder(), "")

    @patch("jobtailor.desktop.platform.system", return_value="Darwin")
    @patch("jobtailor.desktop.subprocess.run")
    def test_other_picker_failure_is_reported(self, run, _platform):
        run.return_value = subprocess.CompletedProcess([], 1, "", "execution error")
        with self.assertRaises(subprocess.CalledProcessError):
            choose_folder()

    @patch("jobtailor.desktop.platform.system", return_value="Darwin")
    @patch("jobtailor.desktop.subprocess.Popen")
    def test_open_folder_passes_path_as_one_argument(self, popen, _platform):
        open_folder("/Users/Test/Job Applications")
        popen.assert_called_once_with(["open", "/Users/Test/Job Applications"])
