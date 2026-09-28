"""Local desktop actions; the app remains bound to localhost."""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path


def choose_folder() -> str:
    if platform.system() == "Darwin":
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'POSIX path of (choose folder with prompt "Choose where to save applications")',
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode and "(-128)" in result.stderr:
            return ""  # User cancelled the native Finder dialog.
        result.check_returncode()
        return result.stdout.strip()
    # Tk needs its own main thread, separate from Streamlit's script thread.
    script = """import json, tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
try:
    print(json.dumps(filedialog.askdirectory(title='Choose where to save applications', mustexist=True)))
finally:
    root.destroy()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout.strip())


def open_folder(path: str) -> None:
    folder = str(Path(path).expanduser().resolve())
    if platform.system() == "Windows":
        os.startfile(folder)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])
