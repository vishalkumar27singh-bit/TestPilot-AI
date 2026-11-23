from __future__ import annotations

import pandas as pd
import streamlit as st

from config.settings import load_user_settings
from core import ai_engine
from core.safety import RedirectError, UnsafeTargetError
from core.test_generator import generate_tests
from core.website_analyzer import analyze_website
from views.common import empty_state, get_db, page_header, pill, tests_dataframe
from views.new_run import execute_and_show


def render():
    page_header("UI testing", "Analyze a page, preview the test plan TestPilot would build, then run it.")
    s = load_user_settings()
    url = st.text_input("Website URL", placeholder="https://your-site.com", key="ui_url")
    allowed = st.checkbox("I own this website or have permission to test it", key="ui_perm")
    c1, c2 = st.columns(2)
    analyze = c1.button("Analyze & preview plan", key="ui_an")
    run = c2.button("Run this plan", type="primary", key="ui_run")
    if analyze or run:
        if not url.strip():
            st.warning("Enter a URL first."); return
    if analyze:
        try:
            with st.spinner("Fetching and analyzing…"):
                profile = analyze_website(url, allow_loopback=s["allow_loopback"], allow_private=s["allow_private"], timeout=s["request_timeout"])
                plan = generate_tests(profile, use_ai=bool(s["use_ai"]) and ai_engine.is_enabled())
            st.session_state["ui_profile"], st.session_state["ui_plan"] = profile, plan
        except RedirectError as e:
            st.error(f"The site answered but its redirects could not be followed over plain HTTP ({e}). Use New Test Run with the browser engine instead."); return
        except (UnsafeTargetError, ValueError) as e:
            st.error(str(e)); return
        except Exception as e:
            st.error(f"Could not analyze the page: {type(e).__name__}"); return
    profile = st.session_state.get("ui_profile")
    if profile:
        st.markdown(f"**{profile.title or '(no title)'}** — {profile.final_url} · HTTP {profile.status_code} · {profile.load_time:.2f}s")
        st.write("Detected functionality: " + (", ".join(profile.features) or "none"))
        t1, t2, t3, t4, t5 = st.tabs(["Headings", "Links", "Buttons & inputs", "Forms & images", "Planned tests"])
        with t1:
            st.dataframe(pd.DataFrame(profile.headings), hide_index=True) if profile.headings else empty_state("No headings found")
        with t2:
            st.dataframe(pd.DataFrame([{k: l[k] for k in ("text", "href", "internal", "in_nav")} for l in profile.links]), hide_index=True) if profile.links else empty_state("No links")
        with t3:
            st.markdown("**Buttons**")
            st.dataframe(pd.DataFrame([{k: b[k] for k in ("text", "type", "id", "disabled")} for b in profile.buttons]), hide_index=True) if profile.buttons else st.caption("None")
            st.markdown("**Inputs**")
            st.dataframe(pd.DataFrame([{k: i[k] for k in ("type", "name", "id", "placeholder", "label", "required")} for i in profile.inputs]), hide_index=True) if profile.inputs else st.caption("None")
        with t4:
            st.dataframe(pd.DataFrame([{k: f[k] for k in ("index", "id", "method", "action", "field_count", "has_submit", "is_search")} for f in profile.forms]), hide_index=True) if profile.forms else st.caption("No forms")
            st.dataframe(pd.DataFrame([{"src": i["src"], "has_alt": i["has_alt"]} for i in profile.images]), hide_index=True) if profile.images else st.caption("No images")
        with t5:
            plan = st.session_state.get("ui_plan", [])
            df = pd.DataFrame([{"ID": t.test_id, "Test": t.name, "Category": t.category, "Severity": t.severity, "Source": t.source, "Expected": t.expected} for t in plan])
            st.dataframe(df, hide_index=True)
    if run:
        if not allowed:
            st.warning("Please confirm you have permission to test this website."); return
        execute_and_show(url, {"engine": "auto", "use_ai": bool(s["use_ai"])}, "ui")
        from views.common import show_run_detail
        show_run_detail(st.session_state["last_run_id"], "uitab") if st.session_state.get("last_run_id") else None
    st.markdown("### Recent UI runs")
    runs = get_db().list_runs("ui", 8)
    if not runs:
        empty_state("No UI runs yet.")
    else:
        st.dataframe(pd.DataFrame([{"Run": r["id"], "URL": r["url"], "Passed": r["passed"], "Failed": r["failed"], "Skipped": r["skipped"], "Engine": r["engine"], "Started": r["started_at"]} for r in runs]), hide_index=True)
