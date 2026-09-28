# Version 0.16 · Source-safe context edition

## What changed

- Added a lightweight context-management layer inspired by Headroom's core principles, without installing its proxy, ML stack, or telemetry beacon.
- Every prompt now has an auditable context budget with explicit input estimate, requested output allowance, and safety reserve. Oversized prompts stop before inference; source is never silently truncated.
- Resume and JD evidence is protected from semantic compression. Every JD fragment is included in logical coverage, including repeated text.
- Byte-identical, temperature-zero requests can reuse a schema-validated response through a bounded, memory-only cache scoped to one local AI client. Different prompts, models, settings, or non-deterministic calls are never reused.
- Each requested job folder now includes `Context_Headroom.json`. `Tailoring_Report.html` also summarizes model requests, cache hits, and largest reserved context usage.
- Context instrumentation fails open: if diagnostics cannot be recorded, normal tailoring and export continue.
- No dependency changes are required. The existing one-job queue, evidence checks, ATS exports, cancellation, sponsorship screening, Telegram alerts, applied history, Windows/macOS launchers, dark neomorphic UI, and local Three.js overview are retained.

## Verification and limits

All 132 automated tests passed. New tests cover budget enforcement, source-preserving exact-repeat behavior, deterministic cache eligibility/isolation, full logical JD coverage, and folder-level context audits. Existing coverage still includes the portal, parsing, evidence tailoring, factual guards, ATS documents, search cancellation, sponsorship, the serial queue, Telegram, applied history, the site shell, bundled graphics, and both platform launchers. Tests use controlled model, website and Telegram responses rather than real accounts or inference.

Bash syntax and JavaScript bundle syntax are checked. The full Streamlit test suite passed. Desktop/mobile appearance, live WebGL animation, native Windows/macOS installers, and actual M5/RTX inference speed still need testing on those devices.

This is a local source ZIP with setup scripts, not a signed standalone desktop binary. Python, dependencies, Ollama and the model must be installed. Version checks allow Python 3.11–3.14; package availability can vary and setup errors must be addressed before use.

The app still relies on parsed resume email (then name) for application history. A permanent applicant profile and cross-device history sync have not been added. Resume or JD failures do not produce a falsely labelled successful tailored resume. Review every generated document before applying.

## Developer references

- [Streamlit v2 components](https://docs.streamlit.io/develop/api-reference/custom-components/st.components.v2.component)
- [Streamlit navigation](https://docs.streamlit.io/develop/api-reference/navigation/st.navigation)
- [Retaining widget state across pages](https://docs.streamlit.io/develop/concepts/multipage-apps/widgets)
- [Three.js documentation](https://threejs.org/docs/)
- [Headroom](https://github.com/headroomlabs-ai/headroom) — architectural inspiration for context budgeting and reuse; no Headroom code or package is bundled

Three.js 0.180.0 is bundled under its MIT license in `jobtailor/assets/THREE-LICENSE.txt`.
