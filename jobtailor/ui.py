"""Presentation helpers for the local Streamlit workspace."""

from html import escape
from pathlib import Path

import streamlit as st


def apply_theme() -> None:
    css = (Path(__file__).parent / "assets" / "workspace.css").read_text(
        encoding="utf-8"
    )
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_header() -> None:
    st.markdown(
        """<div class="workspace-hero portal-hero">
          <div class="workspace-eyebrow"><span class="brand-mark" aria-hidden="true">OA</span>
            YOUR APPLICATION WORKSPACE <span class="version-tag">V0.17</span></div>
          <h1>Make your next <span>move.</span></h1>
          <p>Upload your resume. Find relevant roles. Prepare only the applications you choose.</p>
        </div>""",
        unsafe_allow_html=True,
    )


def render_overview(keywords: int, jobs: int, matches: int, folders: int) -> None:
    # Values come only from the current session, never illustrative data.
    st.markdown(
        '<div class="overview-grid" aria-label="Current session overview">'
        + "".join(
            f'<div class="overview-card"><span class="overview-label">{label}</span>'
            f'<strong>{value:02d}</strong><span class="overview-note">{note}</span></div>'
            for label, value, note in [
                ("Automatic keywords", keywords, "Extracted from your experience"),
                ("Discovered roles", jobs, "In this workspace"),
                ("H1B matches", matches, "Sponsorship or transfer stated"),
                ("Application folders", folders, "Created this session"),
            ]
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def render_sponsorship_status(status: str, allowed: bool) -> None:
    tone = (
        "positive" if allowed else "negative" if status == "Not offered" else "review"
    )
    st.markdown(
        f'<div class="status-badge status-{tone}">'
        f'<span class="status-dot" aria-hidden="true"></span>'
        f"Sponsorship: {escape(status)}</div>",
        unsafe_allow_html=True,
    )
