from __future__ import annotations

import hashlib
from pathlib import Path

import streamlit as st

from jobtailor.application_history import (
    ApplicationHistory,
    candidate_key,
    posting_keys,
)
from jobtailor.application_ui import (
    remember_apply_click,
    render_application_confirmation,
    render_applied_history,
)
from jobtailor.desktop import choose_folder, open_folder
from jobtailor.evidence_tailoring import COMPLETED
from jobtailor.folder_queue import FolderQueue
from jobtailor.models import SearchSettings
from jobtailor.ollama_client import OllamaClient
from jobtailor.search_task import SearchTask
from jobtailor.sponsorship import INCLUDE_ALL, INCLUDE_H1B
from jobtailor.telegram_notifications import TelegramNotifications
from jobtailor.telegram_ui import render_telegram_settings
from jobtailor.ui import apply_theme, render_header
from jobtailor.workspace import prepare_resume

st.set_page_config(
    page_title="OrbitApply",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed",
)
apply_theme()
render_header()

SOURCES = {
    "Indeed": "indeed",
    "Google Jobs": "google",
    "LinkedIn": "linkedin",
    "ZipRecruiter": "zip_recruiter",
    "Glassdoor": "glassdoor",
}
DEFAULTS = {
    "worker_busy": False,
    "prepared_resume_v09": None,
    "job_workspace_v09": None,
    "upload_fingerprint_v09": "",
    "resume_upload_snapshot_v091": None,
    "search_generation_v09": 0,
    "output_path_v09": str(Path.home() / "Documents" / "JobApplications"),
    "folder_queue_v011": None,
    "search_task_v013": None,
    "applied_search_v013": "",
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value

if "telegram_notifications_v012" not in st.session_state:
    st.session_state.telegram_notifications_v012 = TelegramNotifications()


def folders_pending():
    queue = st.session_state.folder_queue_v011
    return bool(queue and queue.busy)


def search_pending():
    task = st.session_state.search_task_v013
    return bool(task and task.busy)


def queue_task(kind, **payload):
    # Callbacks run before the next script render, so Start is disabled BEFORE work.
    if st.session_state.worker_busy or folders_pending() or search_pending():
        return
    st.session_state.worker_busy = True
    st.session_state.pending_task_v09 = {"kind": kind, **payload}


def queue_search():
    if st.session_state.worker_busy or folders_pending() or search_pending():
        return
    try:
        hidden = ApplicationHistory().keys(
            candidate_key(st.session_state.prepared_resume_v09)
        )
    except Exception:  # noqa: BLE001 - explain why past applications cannot be excluded
        st.session_state.action_error_v09 = "Could not read your application history. Check that your user data folder is accessible, then try searching again."
        return
    payload = {
        "excluded_posting_keys": sorted(hidden),
        "prepared": st.session_state.prepared_resume_v09,
        "roles": list(st.session_state.roles_to_search_v09),
        "output": st.session_state.output_path_v09,
        "settings": SearchSettings(
            keywords=[],
            country=st.session_state.country_v09,
            location=st.session_state.location_v09,
            days_old=st.session_state.days_v09,
            sites=[SOURCES[label] for label in st.session_state.sources_v09],
            results_per_keyword=st.session_state.per_query_v09,
            remote_only=st.session_state.remote_v09,
        ),
        "sponsorship": st.session_state.sponsorship_v09,
        "browser": st.session_state.browser_v09,
    }
    st.session_state.search_task_v013 = SearchTask(payload)
    st.session_state.job_workspace_v09 = None
    st.session_state.folder_queue_v011 = None
    st.session_state.search_generation_v09 += 1
    st.session_state.pop("action_error_v09", None)


def cancel_search():
    task = st.session_state.search_task_v013
    if task:
        task.cancel()


def queue_folder(listing_id):
    if st.session_state.worker_busy or search_pending():
        return
    try:
        if st.session_state.folder_queue_v011 is None:
            st.session_state.folder_queue_v011 = FolderQueue(
                st.session_state.job_workspace_v09,
                notifications=st.session_state.telegram_notifications_v012,
            )
        st.session_state.folder_queue_v011.enqueue(
            listing_id,
            model=st.session_state.model_v09,
            context_window=st.session_state.context_v09,
        )
        st.session_state.pop("action_error_v09", None)
    except Exception as exc:  # noqa: BLE001 - report invalid or unavailable queue requests
        st.session_state.action_error_v09 = str(exc)


def cancel_folder(task_id):
    queue = st.session_state.folder_queue_v011
    if queue:
        queue.cancel(task_id)


def retry_resume(name, data, fingerprint):
    queue_task("resume", name=name, data=data, fingerprint=fingerprint)


def capture_resume_upload():
    # Keep bytes independently of the uploader widget during disabled reruns.
    # An explicit removal event clears this snapshot; a transient None does not.
    uploaded = st.session_state.get("resume_upload_v09")
    st.session_state.resume_upload_snapshot_v091 = (
        {"name": uploaded.name, "data": uploaded.getvalue()} if uploaded else None
    )


def browse_output():
    if st.session_state.worker_busy or folders_pending() or search_pending():
        return
    try:
        path = choose_folder()
        if path:
            st.session_state.output_path_v09 = path
    except Exception as exc:  # noqa: BLE001 - native desktop picker may be unavailable
        st.session_state.action_error_v09 = (
            f"Paste a folder path instead; the folder picker could not open. {exc}"
        )


def show_folder(path):
    try:
        open_folder(path)
    except Exception as exc:  # noqa: BLE001 - report platform-specific launch failures
        st.session_state.action_error_v09 = f"Open this folder manually: {path}. {exc}"


search_task = st.session_state.search_task_v013
if search_task:
    result = search_task.snapshot()
    if not result["busy"] and result["id"] != st.session_state.applied_search_v013:
        st.session_state.job_workspace_v09 = result["workspace"]
        st.session_state.applied_search_v013 = result["id"]
        if result["error"]:
            st.session_state.action_error_v09 = result["error"]
folder_busy = folders_pending()
search_busy = search_pending()
busy = st.session_state.worker_busy or folder_busy or search_busy
with st.container(border=True, key="run_controls"):
    st.subheader("Find roles that fit your experience")
    resume = st.file_uploader(
        "Upload your resume",
        type=["pdf", "docx", "txt", "md"],
        disabled=busy,
        key="resume_upload_v09",
        on_change=capture_resume_upload,
    )
    if resume is not None:
        st.session_state.resume_upload_snapshot_v091 = {
            "name": resume.name,
            "data": resume.getvalue(),
        }
    upload = st.session_state.resume_upload_snapshot_v091
    if resume is None and upload:
        st.caption(f"Using your session's uploaded resume: {upload['name']}. Upload another file to replace it.")
    fingerprint = (
        hashlib.sha256(upload["name"].encode() + upload["data"]).hexdigest()
        if upload
        else ""
    )
    if fingerprint != st.session_state.upload_fingerprint_v09 and not busy:
        st.session_state.upload_fingerprint_v09 = fingerprint
        st.session_state.prepared_resume_v09 = None
        st.session_state.job_workspace_v09 = None
        st.session_state.folder_queue_v011 = None
        st.session_state.search_task_v013 = None
        st.session_state.roles_to_search_v09 = []
        st.session_state.pop("reset_role_choices_v09", None)
        st.session_state.pop("action_error_v09", None)
        if upload:
            queue_task(
                "resume",
                name=upload["name"],
                data=upload["data"],
                fingerprint=fingerprint,
            )
            # Finish this render so Streamlit commits the new uploader value.
            # Rerunning immediately with disabled=True can discard that value.
            busy = True

    prepared = st.session_state.prepared_resume_v09
    if st.session_state.pop("reset_role_choices_v09", False):
        st.session_state.roles_to_search_v09 = (
            list(prepared["role_titles"]) if prepared else []
        )
    selected_roles = st.multiselect(
        "Roles to look for",
        options=prepared["role_titles"] if prepared else [],
        key="roles_to_search_v09",
        disabled=busy or not prepared,
        accept_new_options=True,
        placeholder="Suggested roles appear after your resume is read",
        help="Suggested from your uploaded resume. Keep the roles you want, remove others, or type an additional title.",
    )
    if prepared:
        st.caption(
            f"{len(selected_roles)} role(s) selected. Your resume skills will help focus these searches."
        )
        if prepared["warnings"]:
            with st.expander("Resume notes", expanded=False):
                for warning in prepared["warnings"]:
                    st.warning(warning)
    elif upload and not busy:
        st.button(
            "Retry resume analysis",
            on_click=retry_resume,
            args=(upload["name"], upload["data"], fingerprint),
        )

    country_col, location_col = st.columns(2)
    country_col.selectbox(
        "Country",
        [
            "USA",
            "India",
            "Canada",
            "United Kingdom",
            "Australia",
            "Germany",
            "France",
            "Ireland",
            "Luxembourg",
            "Netherlands",
            "Singapore",
            "United Arab Emirates",
        ],
        key="country_v09",
        disabled=busy,
    )
    location_col.text_input(
        "City or region (optional)", key="location_v09", disabled=busy
    )
    path_col, browse_col = st.columns([4, 1], vertical_alignment="bottom")
    output = path_col.text_input(
        "Save requested folders here", key="output_path_v09", disabled=busy
    )
    browse_col.button(
        "Choose folder", on_click=browse_output, width="stretch", disabled=busy
    )
    with st.expander("More options", expanded=False):
        st.selectbox(
            "Sponsorship filter",
            [INCLUDE_ALL, INCLUDE_H1B],
            key="sponsorship_v09",
            disabled=busy,
        )
        st.number_input(
            "Posted within days",
            min_value=1,
            max_value=60,
            value=14,
            key="days_v09",
            disabled=busy,
        )
        source_labels = st.multiselect(
            "Job sources",
            list(SOURCES),
            default=["Indeed", "Google Jobs"],
            key="sources_v09",
            disabled=busy,
        )
        st.number_input(
            "Results requested per query",
            min_value=5,
            max_value=100,
            value=20,
            step=5,
            key="per_query_v09",
            disabled=busy,
        )
        st.checkbox("Remote roles only", key="remote_v09", disabled=busy)
        st.checkbox(
            "Use browser fallback for JavaScript job pages",
            value=True,
            key="browser_v09",
            disabled=busy,
        )
        st.text_input("Local model", value="qwen3:8b", key="model_v09", disabled=busy)
        st.select_slider(
            "Context window",
            options=[4096, 8192, 12288, 16384],
            value=8192,
            key="context_v09",
            disabled=busy,
        )
        st.caption(
            "Source-safe context optimization is always on: the full JD is reviewed in bounded fragments, "
            "resume/JD evidence is never summarized away, oversized prompts stop safely, and only identical "
            "deterministic calls may be reused in memory for the current job."
        )

    render_telegram_settings(st.session_state.telegram_notifications_v012)
    render_applied_history(prepared, show_folder)

    st.button(
        "Start",
        type="primary",
        width="stretch",
        key="start_v09",
        on_click=queue_search,
        disabled=busy
        or not prepared
        or not selected_roles
        or not source_labels
        or not output.strip(),
    )
    if search_busy:
        st.button(
            "Cancel search",
            key="cancel_search_v013",
            on_click=cancel_search,
            width="stretch",
        )
    if busy:
        st.caption(
            "Tailoring is running. You can queue more jobs or cancel waiting jobs below."
            if folder_busy
            else "Searching... You can cancel at any time."
            if search_busy
            else "Reading your resume..."
        )
    else:
        st.caption(
            "Start searches your selected roles. Create a tailored resume folder only for jobs you choose below."
        )

if st.session_state.get("action_error_v09"):
    st.error(st.session_state.action_error_v09)


@st.fragment(run_every=1.0 if search_busy else None)
def render_search_progress():
    task = st.session_state.search_task_v013
    if not task:
        return
    result = task.snapshot()
    if result["busy"] != search_busy:
        st.rerun()
    found = result["workspace"]
    count = len(found["roles"]) if found else 0
    if result["busy"]:
        st.info(f"{result['detail']} · {count} roles found so far")
        if result["total"]:
            st.progress(min(1.0, max(0.0, result["done"] / result["total"])))
    elif result["state"] == "cancelled":
        st.info(
            f"Search cancelled. Kept {count} roles found so far. A website request already in progress may finish in the background."
        )


render_search_progress()


notification_busy = bool(
    st.session_state.folder_queue_v011 and st.session_state.folder_queue_v011.notifying
)


@st.fragment(run_every=1.0 if folder_busy or notification_busy else None)
def render_results():
    render_application_confirmation()
    queue = st.session_state.folder_queue_v011
    snapshot = queue.snapshot() if queue else {"jobs": [], "busy": False}
    # Only re-render the top controls when the queue crosses the idle boundary.
    if (
        snapshot["busy"] != folder_busy
        or snapshot.get("notifying", False) != notification_busy
    ):
        st.rerun()
    workspace = st.session_state.job_workspace_v09
    if not workspace:
        return
    if queue:
        workspace["roles"] = snapshot["roles"]
        workspace["folder"] = snapshot["folder"]
    in_flight = {
        j["listing_id"]: j
        for j in snapshot["jobs"]
        if j["state"] in {"queued", "running"}
    }
    table_area, queue_area = st.columns([4.2, 1.6], gap="large")
    with queue_area, st.container(border=True, key="queue_panel"):
        st.subheader("Folder queue")
        waiting = [j for j in snapshot["jobs"] if j["state"] == "queued"]
        active = next((j for j in snapshot["jobs"] if j["state"] == "running"), None)
        st.caption(f"{1 if active else 0} processing · {len(waiting)} waiting")
        if any("could not" in j.get("notification", "") for j in snapshot["jobs"]):
            st.warning(
                "A Telegram alert could not be confirmed. See Recent activity. Your folder results are unchanged."
            )
        if active:
            st.markdown("**Processing now**")
            st.text(active["role"])
            st.caption(active["company"])
            st.info(active["detail"])
        elif not waiting:
            st.caption(
                "Choose Create folder beside any job. The AI handles one at a time."
            )
        if waiting:
            st.markdown("**Waiting**")
            with st.container(
                height=min(460, max(180, len(waiting) * 175)), border=False
            ):
                for position, item in enumerate(waiting, 1):
                    st.text(f"{position}. {item['role']}")
                    st.caption(item["company"])
                    st.button(
                        "Cancel",
                        key=f"cancel_{item['id']}",
                        on_click=cancel_folder,
                        args=(item["id"],),
                        width="stretch",
                    )
                    st.divider()
        finished = [
            j for j in snapshot["jobs"] if j["state"] not in {"queued", "running"}
        ]
        if finished:
            with st.expander("Recent activity", expanded=False):
                labels = {
                    "done": "Finished",
                    "failed": "Needs review",
                    "cancelled": "Cancelled",
                }
                for item in reversed(finished[-5:]):
                    st.text(f"{labels[item['state']]}: {item['role']}")
                    st.caption(item["company"])
                    if item["state"] == "failed":
                        st.caption(item["detail"])
                    if item.get("notification"):
                        st.caption(item["notification"])
        usage_rows = []
        for role in workspace["roles"]:
            usage = role.get("ai_usage")
            if not usage:
                continue
            observed = usage.get("observed_ollama", {})
            usage_rows.append({
                "Role": role["role"],
                "Company": role["company"],
                "Input tokens (reported)": observed.get("prompt_eval_count", {}).get("total"),
                "Output tokens (reported)": observed.get("eval_count", {}).get("total"),
                "Input usage coverage": f"{observed.get('prompt_eval_count', {}).get('responses_with_metric', 0)}/{usage['responses_received']} responses",
                "Output usage coverage": f"{observed.get('eval_count', {}).get('responses_with_metric', 0)}/{usage['responses_received']} responses",
                "Payload reduction (est. tokens)": usage["payload"]["estimated_input_tokens_removed"],
                "AI requests": usage["attempts"],
                "Compression overhead (ms)": usage["compression_overhead_ms"],
            })
        if usage_rows:
            with st.expander("AI usage", expanded=False):
                st.caption("Per-job totals include retries and failed responses. Payload reduction is a character-based estimate, not measured savings or speedup. Missing usage is left blank. Full coverage details are in AI_Usage.json in the job folder.")
                st.dataframe(usage_rows, hide_index=True)
    with table_area:
        st.subheader("Job results")
        try:
            applied_keys = ApplicationHistory().keys(
                candidate_key(workspace["prepared"])
            )
        except Exception:  # noqa: BLE001 - preserve visible results on a disk failure
            applied_keys = set()
            st.error(
                "Application history could not be read. Previously applied jobs may appear until it is available again."
            )
        applied_rows = [r for r in workspace["roles"] if posting_keys(r) & applied_keys]
        rows = [
            r
            for r in workspace["roles"]
            if r["included"] and not posting_keys(r) & applied_keys
        ]
        hidden_count = workspace.get("applied_hidden", 0) + len(applied_rows)
        st.caption(
            f"{len(workspace['roles'])} roles found · {len(rows)} shown · Search: {', '.join(workspace['selected_roles'])}"
        )
        if hidden_count:
            st.caption(
                f"{hidden_count} previously applied posting(s) hidden. Manage them under Applied jobs."
            )
        if workspace.get("sponsorship_filter") == INCLUDE_H1B:
            st.caption(
                "Includes stated sponsorship offers and eligible or conditional roles. If the visa type is unspecified, confirm H-1B with the employer. Explicit exclusions are hidden."
            )
        st.caption(
            "Create folder adds a job to the queue. Keep choosing while the AI works. Apply and Open folder remain available."
        )
        if not rows:
            st.info(
                "No roles currently match this filter. Sponsorship checks may be incomplete if you cancelled. Try Include All or start another search."
            )
        else:
            page_size = 20
            pages = (len(rows) + page_size - 1) // page_size
            st.session_state.result_page_v09 = min(
                st.session_state.get("result_page_v09", 1), pages
            )
            page = (
                st.number_input(
                    "Results page",
                    min_value=1,
                    max_value=pages,
                    key="result_page_v09",
                    disabled=st.session_state.worker_busy,
                )
                if pages > 1
                else 1
            )
            with st.container(border=True, key="job_table"):
                headers = st.columns([3, 2, 2, 1, 1.5])
                for col, label in zip(
                    headers,
                    ["Role", "Company", "Status", "Application", "Resume folder"],
                    strict=True,
                ):
                    col.markdown(f"**{label}**")
                for row in rows[(int(page) - 1) * page_size : int(page) * page_size]:
                    cols = st.columns([3, 2, 2, 1, 1.5], vertical_alignment="center")
                    item = in_flight.get(row["listing_id"])
                    state_label = (
                        "Processing"
                        if item and item["state"] == "running"
                        else "Queued"
                        if item
                        else row["status"]
                    )
                    cols[0].text(row["role"])
                    cols[1].text(row["company"])
                    cols[1].caption(row.get("sponsorship", "Not checked"))
                    cols[2].caption(state_label)
                    row_key = (
                        f"{st.session_state.search_generation_v09}_{row['listing_id']}"
                    )
                    cols[3].link_button(
                        "Apply",
                        row["url"],
                        key=f"apply_{row_key}",
                        width="stretch",
                        on_click=remember_apply_click,
                        args=(row, workspace["prepared"]),
                    )
                    folder_exists = bool(row["folder"]) and Path(row["folder"]).is_dir()
                    if folder_exists:
                        cols[4].button(
                            "Open folder",
                            key=f"open_{row_key}",
                            on_click=show_folder,
                            args=(row["folder"],),
                            width="stretch",
                        )
                    if item:
                        cols[4].button(
                            state_label,
                            key=f"pending_{row_key}",
                            disabled=True,
                            width="stretch",
                        )
                    elif not folder_exists or row["status"] not in COMPLETED:
                        cols[4].button(
                            "Retry tailoring" if folder_exists else "Create folder",
                            key=f"create_{row_key}",
                            on_click=queue_folder,
                            args=(row["listing_id"],),
                            disabled=st.session_state.worker_busy or search_busy,
                            width="stretch",
                        )
                    if row.get("reason") and not item:
                        with st.expander(
                            f"Why this role needs review: {row['company']}"
                        ):
                            st.write(row["reason"])
                    if row.get("sponsorship_evidence"):
                        with st.expander(f"Sponsorship wording: {row['company']}"):
                            for quote in row["sponsorship_evidence"]:
                                st.text(quote)
                            st.caption(row.get("sponsorship_source", ""))
                    st.divider()
        if workspace["folder"]:
            st.caption(f"Saved folders: {workspace['folder']}")
        if workspace["warnings"]:
            with st.expander("Search notes", expanded=False):
                for warning in workspace["warnings"]:
                    st.warning(warning)


render_results()

# Start discovery only after rendering disabled Start and the cancellation control.
pending_search = st.session_state.search_task_v013
if pending_search and pending_search.snapshot()["state"] == "pending":
    pending_search.start()
    st.rerun()

# Resume analysis runs after its controls render; search and folders use workers.
task = st.session_state.pop("pending_task_v09", None)
if task:
    st.session_state.pop("action_error_v09", None)
    try:
        client = OllamaClient(
            model=st.session_state.model_v09,
            context_window=st.session_state.context_v09,
        )
        with st.status("Working...", expanded=True) as status:
            detail = st.empty()
            if task["kind"] == "resume":
                detail.write(
                    "Reading your resume and suggesting suitable role titles..."
                )
                bundle = prepare_resume(task["name"], task["data"], client)
                if task["fingerprint"] == st.session_state.upload_fingerprint_v09:
                    st.session_state.prepared_resume_v09 = bundle
                    st.session_state.reset_role_choices_v09 = bool(bundle)
            status.update(label="Finished", state="complete", expanded=False)
    except Exception as exc:  # noqa: BLE001 - restore controls after model, network or filesystem failures
        st.session_state.action_error_v09 = str(exc)
    finally:
        st.session_state.worker_busy = False
    st.rerun()

st.caption(
    "OrbitApply 0.17 · Windows + macOS · Source-safe local AI. Your next move."
)
