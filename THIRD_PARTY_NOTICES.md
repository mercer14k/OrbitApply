# Third-party software

The bundled landing-page renderer includes Three.js 0.180.0. Its MIT notice is
preserved in `jobtailor/assets/THREE-LICENSE.txt` and the generated bundle. Build
tool versions are recorded in `package.json`.

Python runtime dependencies are declared in `requirements.txt`: Streamlit,
Pydantic, python-docx, PyMuPDF, pandas, python-jobspy, Beautiful Soup, ReportLab,
and Playwright. Ollama and the chosen model are installed separately. Their own
licenses and terms apply. This is an inventory, not a completed license audit.
Review the distribution terms of installed versions before a public release,
especially [PyMuPDF's AGPL/commercial licensing options](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).

The built-in compressor is original project code implementing conservative
schema and JSON compaction. No Headroom code or external Agent Content
Compression plugin is included. The Headroom-inspired context guard from the
previous version is retained with its attribution.

The owner has not selected a license for the app's original code. Publishing a
repository does not by itself grant an open-source license. Choose and add a
license before advertising this project as open source. This package makes no
claim of trademark clearance for the working name OrbitApply.
