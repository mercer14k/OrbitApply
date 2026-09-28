# OrbitApply 0.17

One package for Windows and Apple-silicon MacBooks. Dark sci-fi overview, Three.js graphics, and the job-application preparation portal.

## 1. Extract this ZIP

Move the extracted `OrbitApply` folder into Documents or another writable folder. Do not run the app from inside the ZIP. Use a fresh extraction on each computer; a Windows virtual environment cannot be reused on a Mac.

## 2. Check the prerequisites

- Python 3.11–3.14, 64-bit. On Windows, enable **Add Python to PATH**. On Apple silicon, use native ARM64/universal2 Python, not Rosetta. If you already have a compatible Python, setup uses it without replacing it.
- Install and open [Ollama](https://ollama.com/download).
- Internet access for the first setup, model download, and later job searches. Allow several GB of free disk space for dependencies, Chromium and the local model.

## 3. Run setup once

| Computer | Setup file | Start file |
| --- | --- | --- |
| Windows PC (including RTX 2070) | `setup_windows.bat` | `run_windows.bat` |
| Apple-silicon Mac (including M5, 16 GB) | `setup_macos.command` | `run_macos.command` |

Double-click your setup file. Wait for **Setup complete**. It creates a private Python environment, installs dependencies and Chromium, and downloads `qwen3:8b`. If setup reports an error, do not proceed as though installation succeeded.

On Mac, if double-clicking a `.command` file does not open it, open Terminal, type `bash` followed by a space, drag the file into Terminal, then press Return. If macOS presents a security warning, review the file and use its normal Open/Privacy & Security controls; do not disable Gatekeeper globally.

## 4. Open the app

Double-click the Start file for your computer. Keep its Terminal/command window open. If a browser does not open, visit [http://127.0.0.1:8501](http://127.0.0.1:8501) in Chrome. The address works only while the app is running on that computer.

You will see the overview first. **Open your portal** or **Portal ↗** opens the app's features. The other top tabs explain the workflow and privacy. **Pause motion** stops the 3D animation. It is automatically removed in Portal to release its graphics resources.

## 5. Use the portal

1. Upload your resume and choose the suggested roles you want.
2. Select your country, optional city, output folder and optional sponsorship filter.
3. Click **Start** to search. No folders are created until you request them.
4. Click **Create folder** beside any job you want. You may queue more jobs; tailoring stays one job at a time.
5. Open the completed folder, review the status/report and resume, then apply yourself. `Context_Headroom.json` records context protection; `AI_Usage.json` records reported model usage and estimated payload reduction.
6. After using **Apply**, confirm **Yes, I applied** only when you have submitted. Confirmed history is saved locally.

Recommended starting configuration for both specified machines: `qwen3:8b`, context `8192`. The app reviews every JD fragment, refuses oversized prompts instead of silently truncating them, and never semantically compresses resume/JD evidence. This is not a hardware performance guarantee. The app cannot guarantee full descriptions from blocked job sites or that every draft will pass its checks. Incomplete results are labelled for review, not misrepresented as tailored resumes.

Stop the app using **Ctrl+C** on Windows or **Control+C** on Mac in the launcher's terminal.

## Upgrading from an older version

Keep your original installation and output folders as a backup. For this update from 0.16, stop the app and copy all source files, the complete `jobtailor` folder, `.streamlit/config.toml`, and launchers, but keep your existing local `.venv` in its current location and preserve personal data. No new dependencies or model download are needed. Start again with your normal launcher. Existing generated resumes are not rewritten.

Application history is outside the app folder and is not automatically synchronized between computers. It still uses the parsed resume email (or name fallback), not a permanent applicant profile. Use the same email to share history between resume versions on one computer.

## For developers

The prebuilt `jobtailor/assets/landing.bundle.js` includes Three.js 0.180.0 (MIT license included). Users do not need npm. To edit the scene, install Node.js, run `npm install`, edit `jobtailor/assets/landing.js`, then run `npm run build:artwork`. Frontend code uses Streamlit's [v2 component API](https://docs.streamlit.io/develop/api-reference/custom-components/st.components.v2.component) and page navigation. Re-run `python -m unittest discover -s tests -q` after changes. This is not a signed standalone Mac or Windows desktop binary.
