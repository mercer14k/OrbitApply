"""Local-only marketing shell. No user content is injected into HTML/JavaScript."""
from pathlib import Path

import streamlit as st
from streamlit.components.v2 import component

ASSETS = Path(__file__).parent / "assets"

# These input values must survive Streamlit's per-page widget cleanup. Uploads
# already have their own byte snapshot in the portal; button/uploader state must
# NOT be assigned here. Do not persist bot-token widgets beyond their session.
PORTAL_WIDGET_KEYS = (
    "roles_to_search_v09", "country_v09", "location_v09", "output_path_v09",
    "sponsorship_v09", "days_v09", "sources_v09", "per_query_v09",
    "remote_v09", "browser_v09", "model_v09", "context_v09",
)


def preserve_portal_state():
    for key in PORTAL_WIDGET_KEYS:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def portal_busy():
    return bool(st.session_state.get("worker_busy") or any(
        task and task.busy for task in (
            st.session_state.get("folder_queue_v011"),
            st.session_state.get("search_task_v013"),
        )
    ))


def render_navigation(pages, current):
    with st.container(key="shell_nav"):
        columns = st.columns([3.2, 1.05, 1.5, 1, 1.15], vertical_alignment="center", gap="small")
        with columns[0]:
            st.markdown('<div class="shell-brand"><span class="shell-logo" aria-hidden="true">↗</span><div>OrbitApply<span>YOUR NEXT MOVE. LOCALLY POWERED.</span></div></div>', unsafe_allow_html=True)
        for column, (label, target) in zip(columns[1:], pages.items()):
            with column:
                with st.container(key="nav_" + label.lower().replace(" ", "_") + ("_active" if label == current else "")):
                    st.page_link(target, label=("Portal ↗" if label == "Portal" else label), width="stretch", disabled=portal_busy() and label != "Portal")
    if portal_busy():
        st.caption("Stay in the portal while work finishes. Search cancellation and folder-queue controls remain available below.")


@st.cache_resource
def landing_component():
    return component(
        "orbitapply_overview_v017",
        html=(ASSETS / "landing.html").read_text(encoding="utf-8"),
        css=(ASSETS / "landing.css").read_text(encoding="utf-8"),
        js=(ASSETS / "landing.bundle.js").read_text(encoding="utf-8"),
    )


def render_landing():
    result = landing_component()(key="overview_scene", on_route_change=lambda: None)
    if result.route == "portal":
        st.switch_page("app.py")
    if result.route == "guide":
        st.switch_page(st.Page(render_guide, title="How it works", url_path="guide"))
    # A native control remains available if a browser blocks custom JavaScript.
    with st.container(key="portal_fallback"):
        st.page_link("app.py", label="Open the application portal", icon="↗")


def render_guide():
    st.markdown('<div class="info-heading"><p>THE WORKFLOW / 01</p><h1>Less busywork.<br>More possibility.</h1><span>One resume. A considered search. Application folders you control.</span></div>', unsafe_allow_html=True)
    for number, title, body in [
        ("01", "Start with your experience", "Open Portal and upload a text-based PDF, DOCX, TXT, or Markdown resume. The local model reads it and suggests relevant roles. Keep the titles you want or add your own."),
        ("02", "Find your next direction", "Choose country, optional city, and an output folder. More options includes the sponsorship filter and a default 14-day date window. Start searches; it does not create folders. Cancel search keeps partial results."),
        ("03", "Create only what you need", "Click Create folder beside a role. Keep adding jobs to the queue while one resume is being tailored. You can cancel waiting jobs. The app retrieves the JD, maps requirements to resume evidence, and exports the result."),
        ("04", "Review, then apply yourself", "Open the folder in Finder or File Explorer. Read STATUS.txt and the tailoring report. A successful draft includes Tailored_Resume.pdf and .docx; incomplete or unchanged results are labelled differently. Apply opens the employer page. Confirm Yes, I applied only after submitting."),
    ]:
        st.markdown(f'<article class="guide-step"><span>{number}</span><div><h2>{title}</h2><p>{body}</p></div></article>', unsafe_allow_html=True)
    with st.expander("What is saved in each folder?"):
        st.write("The application link, retrieved job description, status, requirement/evidence reports, and the completed resume draft where generation succeeds. An early retrieval or model failure may leave no finished resume; it is never labelled as successfully tailored.")
        st.write("Reviewable PDF/DOCX files use a single-column, selectable-text layout. No format guarantees success with every ATS. Scanned resumes need OCR before uploading.")
    with st.expander("Local model settings and setup"):
        st.write("Use qwen3:8b with an 8192-token context as the starting configuration for an RTX 2070 or Apple-silicon Mac with 16 GB memory. The queue processes one selected job at a time; long JDs are split into bounded requests. A source-safe context layer checks every prompt and can reuse only byte-identical deterministic calls in memory. Speed depends on the model, input, and available memory.")
        st.write("Windows: run setup_windows.bat once, then run_windows.bat. Mac: run setup_macos.command once, then run_macos.command. Keep the launcher's Terminal window open. Python, Ollama, dependencies and the model are prerequisites; this ZIP is not a standalone signed .app or .exe.")
    with st.expander("Does sponsorship history mean a role sponsors?"):
        st.write("No. Include H1B only looks for explicit general, H-1B, or conditional sponsorship wording in the retrieved role text. No-sponsorship statements exclude a role. General offers do not confirm H-1B specifically. Include All does not filter on sponsorship. Confirm eligibility with the employer.")
    st.page_link("app.py", label="Open Portal ↗", width="content")


def render_privacy():
    st.markdown('<div class="info-heading"><p>BUILT FOR YOUR COMPUTER / 02</p><h1>Your career.<br>Your control.</h1><span>A clear view of what stays local and what leaves your device.</span></div>', unsafe_allow_html=True)
    for title, body in [
        ("Local AI, local documents", "With the default local Ollama endpoint, resume parsing and tailoring run on your computer. Resume files, generated documents, and application history are stored locally. Context diagnostics and identical-call reuse stay in memory and send no telemetry. The overview graphics, Three.js code, and fonts are bundled or system-provided; the landing page needs no CDN or analytics service."),
        ("Searches contact external websites", "Search keywords derived from your resume, selected role titles, location, and page requests are sent to job-search services and employers' sites. Those services can see your IP address and query information. Local processing does not mean the entire workflow is offline."),
        ("Telegram is optional", "If you connect Telegram, alerts send the company, role, application URL, folder path and status through Telegram. The connection is kept in the current app session. Resume documents are not attached to those alerts."),
        ("You submit applications", "The app does not create employer accounts or submit forms. Opening Apply does not mark a job as applied; only your Yes confirmation does. Confirmed applications are stored in a local database outside the installation folder."),
        ("History follows the resume identity", "The current app matches history using the parsed email, with the parsed name as a fallback. The same email keeps history across resumes and filters. A different email may create separate history. History does not automatically sync between your Windows and Mac devices; different posting IDs can still reappear."),
        ("You review the evidence", "The AI is instructed to preserve facts and match requirements to source evidence. Checks can miss mistakes. Review the resume, job description, and tailoring report before submitting. AI output is a draft, not a guarantee of qualifications or eligibility."),
    ]:
        st.markdown(f'<article class="privacy-card"><h2>{title}</h2><p>{body}</p></article>', unsafe_allow_html=True)
    st.page_link("app.py", label="Open Portal ↗", width="content")
