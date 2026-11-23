"""Run panel (URL, permission, quick config, live logs) reused by Overview, New Test Run and UI Testing."""
from __future__ import annotations

from html import escape

import streamlit as st

from config.settings import load_user_settings
from core import ai_engine
from core.safety import UnsafeTargetError, check_availability, validate_url
from core.test_executor import RunAborted, run_website_test
from views.common import get_db, page_header, show_run_detail


def target_preview(url: str, settings: dict):
    """Display the target before testing: validated host, resolved IPs, availability."""
    try:
        t = validate_url(url, settings["allow_loopback"], settings["allow_private"])
    except UnsafeTargetError as e:
        st.error(f"Cannot test this target: {e}")
        return False
    av = check_availability(t.url, settings["allow_loopback"], settings["allow_private"], settings["request_timeout"])
    if av["redirect_unresolved"]:
        state = (f"Reachable · last response HTTP {av['status']} at {av['final_url']} · redirects could not be fully resolved over HTTP "
                 f"(not treated as unavailable; the browser engine will be used)")
    elif av["ok"]:
        state = f"HTTP {av['status']} · {av['time']:.2f}s" + (f" · final URL: {av['final_url']}" if av["redirects"] else "")
    else:
        state = f"Unavailable: {av['error']}"
    st.markdown(f"<div class='card target'><div><div class='k'>Target website</div><div class='v'>{escape(t.url)}</div></div>"
                f"<div><div class='k'>Host</div><div class='v'>{escape(t.host)}</div></div><div><div class='k'>Resolved IP</div><div class='v'>{escape(', '.join(t.ips))}</div></div>"
                f"<div><div class='k'>Availability</div><div class='v'>{escape(state)}</div></div></div>", unsafe_allow_html=True)
    for n in av["notes"]:
        st.warning(n)
    return av["ok"]


def execute_and_show(url: str, cfg: dict, key: str):
    """Runs the workflow with live logs/progress and stores the run id in session state."""
    status_box = st.empty()
    bar = st.progress(0.0)
    st.markdown("**Live execution log**")
    log_box = st.empty()

    def on_log(line, entries):
        log_box.code("\n".join(entries[-14:]), language="text")

    def on_progress(frac, msg):
        bar.progress(min(max(frac, 0.0), 1.0))
        status_box.markdown(f"<div class='card'><b>Now:</b> {escape(msg)}</div>", unsafe_allow_html=True)

    try:
        run_id = run_website_test(url, cfg, on_log, on_progress, get_db())
        st.session_state["last_run_id"] = run_id
        status_box.success(f"Run #{run_id} complete.")
        return run_id
    except RunAborted as e:
        status_box.error(str(e))
        return None


def run_panel(key: str, advanced: bool = False):
    s = load_user_settings()
    ai = ai_engine.status()
    url = st.text_input("Website URL", placeholder="https://your-site.com", key=f"{key}_url", label_visibility="collapsed")
    allowed = st.checkbox("I own this website or have permission to test it", key=f"{key}_perm")
    with st.expander("Quick configuration", expanded=advanced):
        c1, c2, c3 = st.columns(3)
        engine = c1.selectbox("Browser engine", ["auto", "selenium", "static"], key=f"{key}_engine",
                              help="auto = Selenium Chrome if available, otherwise static HTTP checks. static = no browser (no screenshots).")
        use_ai = c2.checkbox("Use AI features", value=ai["enabled"], disabled=not ai["enabled"], key=f"{key}_ai",
                             help="AI key not configured: the rule-based engine is used." if not ai["enabled"] else f"{ai['provider']} · {ai['model']}")
        via_pytest = c3.checkbox("Run through pytest", key=f"{key}_pytest", help="Executes each generated test as a parametrized pytest case.")
        c4, c5, c6, c7, c8 = st.columns(5)
        o_links = c4.checkbox("Broken links", True, key=f"{key}_o_l")
        o_forms = c5.checkbox("Forms", True, key=f"{key}_o_f")
        o_resp = c6.checkbox("Responsive", True, key=f"{key}_o_r")
        o_con = c7.checkbox("Console errors", True, key=f"{key}_o_c")
        o_click = c8.checkbox("Click safe buttons", True, key=f"{key}_o_k", help="Real browser only. Buttons in forms or named like delete/pay/send/submit are never clicked.")
        max_links = st.slider("Max links to check", 5, 50, s["max_links"], key=f"{key}_ml")
    b1, b2 = st.columns([1, 1])
    check = b1.button("Check target", key=f"{key}_check")
    start = b2.button("Start AI Testing", type="primary", key=f"{key}_start")
    if (check or start) and not url.strip():
        st.warning("Enter a website URL first.")
        return
    if check:
        target_preview(url, s)
    if start:
        if not allowed:
            st.warning("Please confirm you own the website or have permission to test it.")
            return
        if not target_preview(url, s):
            return
        cfg = {"engine": engine, "use_ai": use_ai and ai["enabled"], "via_pytest": via_pytest, "max_links": max_links,
               "options": {"links": o_links, "forms": o_forms, "responsive": o_resp, "console": o_con, "click": o_click}}
        execute_and_show(url, cfg, key)
    rid = st.session_state.get("last_run_id")
    if rid and not start and get_db().get_run(rid):
        st.markdown("---")
        st.markdown("#### Latest results")
        show_run_detail(rid, key=f"{key}_last")
    elif start and st.session_state.get("last_run_id"):
        st.markdown("---")
        show_run_detail(st.session_state["last_run_id"], key=f"{key}_new")


def render():
    page_header("New test run", "Enter a site you are allowed to test. TestPilot validates it, analyzes it, generates tests, runs them and reports bugs.")
    run_panel("nr", advanced=True)
