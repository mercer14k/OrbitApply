"""Apply tracking: opening a posting only asks; only Yes records an application."""

from copy import deepcopy

import streamlit as st

from .application_history import ApplicationHistory, candidate_key, posting_keys


def remember_apply_click(row, prepared):
    pending = st.session_state.setdefault("pending_applications_v014", [])
    candidate = candidate_key(prepared)
    if not any(
        item["candidate"] == candidate and posting_keys(item["row"]) & posting_keys(row)
        for item in pending
    ):
        pending.append({"candidate": candidate, "row": deepcopy(row)})


@st.dialog("Did you apply?", dismissible=False)
def _confirmation(item):
    row = item["row"]
    st.write("Did you submit an application for this posting?")
    st.text(row["role"])
    st.caption(row["company"])
    yes, no = st.columns(2)
    if yes.button(
        "Yes, I applied", key="confirm_applied_v014", type="primary", width="stretch"
    ):
        try:
            ApplicationHistory().mark_applied(item["candidate"], row)
        except Exception:  # noqa: BLE001 - never hide a role when the persistent write fails
            st.error(
                "Your application could not be saved. Check that your user data folder is writable, then try again. The posting has not been hidden."
            )
        else:
            st.session_state.pending_applications_v014.pop(0)
            st.rerun()
    if no.button("No, not yet", key="not_applied_v014", width="stretch"):
        st.session_state.pending_applications_v014.pop(0)
        st.rerun()
    st.caption(
        "Yes hides this posting from future searches. You can undo it in Applied jobs."
    )


def render_application_confirmation():
    pending = st.session_state.get("pending_applications_v014", [])
    if pending:
        _confirmation(pending[0])


def render_applied_history(prepared, open_folder):
    if not prepared:
        return
    with st.expander("Applied jobs", expanded=False):
        candidate = candidate_key(prepared)
        history = ApplicationHistory()
        try:
            records = history.list_applied(candidate)
        except Exception:  # noqa: BLE001 - history failure does not stop the app
            st.error(
                "Application history could not be read. Check your user data folder."
            )
            return
        st.caption(
            f"{len(records)} confirmed application(s). Saved on this computer across restarts and app updates."
        )
        if not records:
            st.write(
                "Click Apply beside a job. When you return, choose Yes only after submitting your application."
            )
        for record in records[:50]:
            label, action = st.columns([4, 1])
            label.text(f"{record['role']} · {record['company']}")
            label.caption(f"Applied {record['applied_at'][:10]}")
            label.link_button("Open posting", record["url"])
            if record["folder"]:
                label.button(
                    "Open resume folder",
                    key=f"history_folder_{record['id']}",
                    on_click=open_folder,
                    args=(record["folder"],),
                )
            if action.button("Undo", key=f"undo_applied_{record['id']}"):
                try:
                    history.undo(candidate, record["id"])
                except Exception:  # noqa: BLE001 - show write failure without discarding history
                    st.error("Could not undo this application. Please try again.")
                else:
                    st.rerun()
        if len(records) > 50:
            st.caption(
                "Showing the latest 50 applications. All confirmed applications are excluded from searches."
            )
