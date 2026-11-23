from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from views.common import empty_state, get_db, page_header, render_bug


def render():
    page_header("Bug reports", "Created automatically for every failed test.")
    db = get_db()
    bugs = db.list_bugs()
    if not bugs:
        return empty_state("No bugs recorded. Failed tests create bug reports automatically.")
    c1, c2, c3 = st.columns(3)
    sev = c1.multiselect("Severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"], default=["CRITICAL", "HIGH", "MEDIUM", "LOW"], key="b_sev")
    runs = sorted({b["run_id"] for b in bugs}, reverse=True)
    run = c2.selectbox("Run", ["All"] + runs, key="b_run")
    status = c3.selectbox("Status", ["All", "OPEN", "FIXED", "WONT_FIX"], key="b_status")
    shown = [b for b in bugs if b["severity"] in sev and (run == "All" or b["run_id"] == run) and (status == "All" or b["status"] == status)]
    st.caption(f"{len(shown)} of {len(bugs)} bug(s)")
    if shown:
        buf = io.StringIO(); pd.DataFrame(shown).to_csv(buf, index=False)
        st.download_button("⬇ Export bugs (CSV)", buf.getvalue(), "testpilot_bugs.csv", "text/csv", key="b_export")
    for b in shown:
        render_bug(b)
        new = st.selectbox("Status", ["OPEN", "FIXED", "WONT_FIX"], index=["OPEN", "FIXED", "WONT_FIX"].index(b["status"] or "OPEN"), key=f"bs_{b['bug_id']}", label_visibility="collapsed")
        if new != b["status"]:
            db.set_bug_status(b["bug_id"], new)
            st.rerun()
