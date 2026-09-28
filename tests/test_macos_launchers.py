from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MacLauncherTests(unittest.TestCase):
    def test_launchers_have_valid_bash_syntax(self):
        for filename in (
            "macos_common.sh",
            "setup_macos.command",
            "run_macos.command",
            "update_macos.command",
        ):
            result = subprocess.run(
                ["bash", "-n", str(ROOT / filename)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_latest_supported_python_and_arm64_are_checked(self):
        common = (ROOT / "macos_common.sh").read_text(encoding="utf-8")
        self.assertIn("python3.14", common)
        self.assertIn("python3.13", common)
        self.assertIn('platform.machine() == "arm64"', common)
        self.assertIn("(3, 14)", common)

    def test_m5_defaults_are_memory_bounded(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        setup = (ROOT / "setup_macos.command").read_text(encoding="utf-8")
        run = (ROOT / "run_macos.command").read_text(encoding="utf-8")
        self.assertIn('value="qwen3:8b"', app)
        self.assertIn("value=8192", app)
        self.assertIn("pull qwen3:8b", setup)
        self.assertIn("streamlit run launch.py", run)


if __name__ == "__main__":
    unittest.main()
