from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from config.settings import load_user_settings
from testing.api_tests import ApiConfigError
from core.test_executor import run_api_test
from views.common import empty_state, get_db, page_header, show_run_detail


def render():
    page_header("API testing", "Send a real request and verify status, speed, headers, JSON validity and structure.")
    s = load_user_settings()
    c1, c2 = st.columns([1, 4])
    method = c1.selectbox("Method", ["GET", "POST", "PUT", "DELETE"], key="api_m")
    url = c2.text_input("Endpoint URL", placeholder="https://api.example.com/items", key="api_url")
    destructive = method != "GET"
    body = ""
    if method in ("POST", "PUT"):
        body = st.text_area("JSON request body (safe editor — validated before sending)", value='{\n  "name": "example"\n}', height=130, key="api_body")
        try:
            json.loads(body) if body.strip() else None
            st.caption("✔ Valid JSON")
        except ValueError as e:
            st.caption(f"✘ Invalid JSON: {e}")
    with st.expander("Headers & expectations"):
        hdf = st.data_editor(pd.DataFrame({"Header": ["Authorization"], "Value": [""]}), num_rows="dynamic", key="api_hdrs", hide_index=True)
        e1, e2, e3 = st.columns(3)
        exp_status = e1.number_input("Expected status (0 = any 2xx)", 0, 599, 0, key="api_es")
        max_ms = e2.number_input("Max response time (ms)", 50, 60000, 2000, key="api_ms")
        keys = e3.text_input("Expected JSON keys (comma-separated)", key="api_keys")
    confirm = True
    if destructive:
        st.warning(f"{method} can modify or delete data on the target server.")
        confirm = st.checkbox(f"I understand this {method} request may change data and I have permission to send it", key="api_confirm")
    if st.button("Run API tests", type="primary", key="api_run"):
        headers = {str(r["Header"]).strip(): str(r["Value"]) for _, r in hdf.iterrows() if str(r["Header"]).strip() and str(r["Value"]).strip() not in ("", "None")}
        try:
            with st.spinner("Sending request…"):
                rid = run_api_test(url, method, headers, body, int(exp_status) or None, int(max_ms), [k.strip() for k in keys.split(",") if k.strip()],
                                   confirm and destructive, s, get_db())
            st.session_state["api_last"] = rid
        except ApiConfigError as e:
            st.error(str(e))
    rid = st.session_state.get("api_last")
    if rid and get_db().get_run(rid):
        st.markdown("#### Result")
        show_run_detail(rid, "api")
        run = get_db().get_run(rid)
        try:
            info = json.loads(run["profile_json"] or "{}")
            with st.expander("Raw response"):
                st.write(f"Status: {info.get('status')} · {info.get('elapsed_ms')} ms")
                st.json(info.get("headers", {}))
                st.code(info.get("body_preview") or "(empty)", language="json")
        except ValueError:
            pass
    st.markdown("### Recent API runs")
    runs = get_db().list_runs("api", 8)
    if runs:
        st.dataframe(pd.DataFrame([{"Run": r["id"], "Endpoint": r["url"], "Passed": r["passed"], "Failed": r["failed"], "Started": r["started_at"]} for r in runs]), hide_index=True)
    else:
        empty_state("No API runs yet.")
