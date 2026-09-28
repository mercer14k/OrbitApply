"""Local desktop entrypoint: overview, guidance, privacy and the existing portal."""
import streamlit as st

from jobtailor.site_shell import render_landing, render_guide, render_privacy, render_navigation, preserve_portal_state, portal_busy
from jobtailor.ui import apply_theme

st.set_page_config(page_title="OrbitApply", page_icon="✦", layout="wide", initial_sidebar_state="collapsed")
apply_theme()
preserve_portal_state()
pages = {
    "Overview": st.Page(render_landing, title="Overview", default=True),
    "How it works": st.Page(render_guide, title="How it works", url_path="guide"),
    "Privacy": st.Page(render_privacy, title="Privacy", url_path="privacy"),
    "Portal": st.Page("app.py", title="Portal", url_path="portal"),
}
page = st.navigation(list(pages.values()), position="hidden")
# A bookmarked route should not mount a 3D scene while this session is working.
if portal_busy() and page.title != "Portal":
    st.switch_page(pages["Portal"])
render_navigation(pages, page.title)
page.run()
