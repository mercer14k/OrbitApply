# OrbitApply 0.17 · MacBook M-series edition

This package is prepared for an Apple-silicon MacBook, including M5 models with 16 GB unified memory. It uses the native ARM64 Python already installed on the Mac and runs the `qwen3:8b` model locally through Ollama. The default 8192-token context is intentional: it leaves memory available for macOS, the browser, and document generation while the app processes one resume at a time.

## Install once

1. Install and open Ollama from https://ollama.com/download/mac if it is not already installed.
2. Extract the ZIP. Move the **OrbitApply Mac** folder to Documents or Applications. Do not run it from inside the ZIP.
3. Double-click **setup_macos.command** and leave Terminal open. It detects a native Apple-silicon Python 3.11 through 3.14, creates a private `.venv`, installs the app packages and Chromium, and downloads `qwen3:8b`.
4. Wait until Terminal says **Setup complete for this Apple-silicon Mac**.

The installer uses your newest compatible installed Python. It does not replace or modify the system Python. If your existing `.venv` came from Windows, Intel macOS, or an unsupported Python, setup moves it to a timestamped backup before creating the correct environment.

## Start the app

Double-click **run_macos.command**. Keep its Terminal window open and use the browser page that opens. If Chrome does not open automatically, visit http://127.0.0.1:8501.

Press **Control+C** in Terminal to stop the app.

If macOS blocks a `.command` file, Control-click it, choose **Open**, then choose **Open** again. If it still does not launch, open Terminal, type `bash` followed by a space, drag the `.command` file into Terminal, and press Return.

## Recommended settings for M5 with 16 GB RAM

- Local model: `qwen3:8b`
- Context window: `8192`
- Keep one folder actively tailoring at a time. The built-in queue already enforces this.
- Do not run another large local model at the same time.
- Keep the Mac connected to power for a long tailoring queue.

The Mac's GPU is used automatically by Ollama through Metal. Seeing combined CPU/GPU activity is normal. Increasing the context to 12288 or 16384 consumes more unified memory and usually makes tailoring slower; the app already chunks long job descriptions and protects each request with a context budget, so a larger context is normally unnecessary.

## Workflow

1. Upload one resume.
2. Review the suggested roles and choose which roles to search.
3. Choose country, location, sponsorship preference, and output folder.
4. Click **Start**. You can cancel an active search and keep partial results.
5. Use **Apply** to open a job. When you return, confirm whether you applied.
6. Use **Create folder** for the jobs you want. You may queue several jobs, but the local AI tailors them one at a time.
7. Each completed folder includes the job description, application link, ATS-readable PDF/DOCX resume, status, tailoring evidence report, and `Context_Headroom.json` audit. Review the files before applying.

Application history is stored at `~/Library/Application Support/LocalJobTailor/application_history.sqlite3`, outside the app folder, so it remains after an app update. The same parsed resume email keeps the same history across different resume versions and search filters.

## Update or repair

Close the running app, then double-click **update_macos.command**. If Python itself changed and the saved environment is reported as incompatible, run **setup_macos.command** again.

Your resumes and generated folders stay on your Mac. Job discovery and job-description retrieval contact public websites. The app does not automatically submit employer forms or create accounts.
