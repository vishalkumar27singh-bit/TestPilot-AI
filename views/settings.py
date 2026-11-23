from __future__ import annotations

import shutil
from importlib.util import find_spec
from pathlib import Path

import streamlit as st

from config.settings import DB_PATH, SCREENSHOT_DIR, VERSION, load_user_settings, save_user_settings
from core import ai_engine
from views.common import metric_cards, page_header

def _has(mod: str) -> bool:
    try:
        return find_spec(mod) is not None
    except (ValueError, ImportError):
        return False


CHROME_PATHS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium"]


def render():
    page_header("Settings", "Safety, engine and environment. Secrets are read from .env and never shown here.")
    s = load_user_settings()
    ai = ai_engine.status()
    st.markdown("#### AI")
    if ai["enabled"]:
        st.success(f"AI features active · provider: {ai['provider']} · model: {ai['model']}")
    else:
        st.info("No AI key configured. The rule-based engine is used for test generation and failure explanations. "
                "Add ANTHROPIC_API_KEY or OPENAI_API_KEY to your .env file (local) or to Streamlit secrets (Community Cloud) and restart to enable AI.")
    st.markdown("#### Safety & engine")
    with st.form("settings_form"):
        lb = st.checkbox("Allow localhost / 127.0.0.1 targets (for the bundled sample site only)", s["allow_loopback"])
        pv = st.checkbox("Allow private-network targets (10.x, 192.168.x, …) — NOT recommended", s["allow_private"])
        c1, c2, c3 = st.columns(3)
        headless = c1.checkbox("Headless browser", s["headless"])
        rt = c2.number_input("Request timeout (s)", 2, 120, int(s["request_timeout"]))
        pt = c3.number_input("Page-load timeout (s)", 5, 180, int(s["page_load_timeout"]))
        ml = st.number_input("Default max links to check", 5, 100, int(s["max_links"]))
        use_ai = st.checkbox("Use AI when available", s["use_ai"])
        if st.form_submit_button("Save settings", type="primary"):
            save_user_settings({"allow_loopback": lb, "allow_private": pv, "headless": headless, "request_timeout": int(rt),
                                "page_load_timeout": int(pt), "max_links": int(ml), "use_ai": use_ai})
            st.success("Settings saved.")
    if s["allow_private"]:
        st.warning("Private-network targets are enabled. Only use this on a network you control.")
    st.markdown("#### Environment check")
    from testing.browser_config import describe_missing, resolve_browser_config
    bcfg = resolve_browser_config(True)
    chrome = bcfg.binary or next((p for p in CHROME_PATHS if Path(p).exists()), None) or shutil.which("google-chrome") or shutil.which("chromium")
    checks = [("Selenium installed", _has("selenium")), ("Chrome found", bool(chrome)), ("Pytest installed", _has("pytest")),
              ("Plotly installed", _has("plotly")), ("ReportLab (PDF)", _has("reportlab"))]
    metric_cards([(n, "OK" if ok else "Missing", "", "pass" if ok else "fail") for n, ok in checks])
    if not (_has("selenium") and chrome):
        st.warning("Browser testing is unavailable here, so runs will use the static HTTP engine (no screenshots, console or layout checks). "
                   + ("Install selenium (pip install selenium). " if not _has("selenium") else "") + (describe_missing(bcfg) if not chrome else ""))
    from testing.browser_config import diagnose
    dg = diagnose(s["headless"])
    with st.expander("Browser diagnostics", expanded=not dg["ready"]):
        st.markdown(f"**Python running TestPilot:** `{dg['python']}` ({dg['python_version']})  \n**Platform:** {dg['platform']} / {dg['arch']}  \n"
                    f"**Selenium:** {dg['selenium'] or 'NOT installed in this Python'}  \n"
                    f"**Browser:** {dg['browser_binary'] or 'not found'} {('(' + dg['browser_version'] + ')') if dg['browser_version'] else ''}  \n"
                    f"**Driver:** {dg['driver']}  \n**Headless:** {dg['headless']}")
        if not dg["selenium"]:
            st.code(dg["install_command"], language="bash")
            st.caption("Run this in a terminal, then restart Streamlit. Start the app with `python -m streamlit run app.py` so the same Python is used.")
    st.caption(f"TestPilot AI v{VERSION} · database: {DB_PATH} · screenshots: {SCREENSHOT_DIR}")
    st.markdown("#### Sample site")
    st.markdown("Start the bundled demo site (with deliberate defects) in a second terminal, enable **Allow localhost** above, then test `http://127.0.0.1:8000/index.html`:")
    st.code("python sample_site/serve.py", language="bash")
    st.markdown("Demo API endpoints: `/api/health`, `/api/items`, `/api/items/1`, `/api/broken` (invalid JSON), `/api/slow` (2.5 s).")
