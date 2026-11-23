"""TestPilot AI — Automated Testing Agent. Run with:  streamlit run app.py"""
import streamlit as st

from config.settings import APP_NAME, APP_TAGLINE, VERSION, ai_provider

st.set_page_config(page_title=f"{APP_NAME} — {APP_TAGLINE}", page_icon="🧭", layout="wide", initial_sidebar_state="expanded")

from views import api_testing, bugs, history, new_run, overview, screenshots, settings, ui_testing  # noqa: E402
from views.common import load_css  # noqa: E402

PAGES = {
    "◎  Overview": overview.render,
    "▶  New Test Run": new_run.render,
    "▤  UI Testing": ui_testing.render,
    "⇄  API Testing": api_testing.render,
    "◷  History": history.render,
    "⚑  Bug Reports": bugs.render,
    "▣  Screenshots": screenshots.render,
    "⚙  Settings": settings.render,
}
KEYS = {label.split("  ", 1)[1]: label for label in PAGES}  # "History" -> "◷  History"

load_css()
if "pending_nav" in st.session_state:  # programmatic navigation (must run before the radio is created)
    st.session_state["nav"] = KEYS.get(st.session_state.pop("pending_nav"), "◎  Overview")

with st.sidebar:
    st.markdown("<div class='brand'><div class='mark'><svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='2.4' "
                "stroke-linecap='round' stroke-linejoin='round'><path d='M4 13l5 5L20 6'/></svg></div><div><b>TestPilot AI</b><small>Automated Testing Agent</small></div></div>",
                unsafe_allow_html=True)
    page = st.radio("Navigation", list(PAGES), key="nav", label_visibility="collapsed")
    mode = f"AI active ({ai_provider()})" if ai_provider() else "Rule-based mode (no AI key)"
    st.markdown(f"<div class='side-note'>{mode}<br>v{VERSION}<br><br>Only test sites you own or have permission to test.</div>", unsafe_allow_html=True)

PAGES[page]()
