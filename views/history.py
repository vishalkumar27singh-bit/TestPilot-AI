from __future__ import annotations

import pandas as pd
import streamlit as st

from utils.helpers import fmt_duration
from views.common import empty_state, get_db, page_header, show_run_detail


def render():
    page_header("Test history", "Every session is stored in SQLite. Open, compare or delete runs.")
    db = get_db()
    runs = db.list_runs()
    if not runs:
        return empty_state("No test history yet.")
    st.dataframe(pd.DataFrame([{"Run": r["id"], "Type": r["kind"].upper(), "URL": r["url"], "Status": r["status"], "Passed": r["passed"], "Failed": r["failed"],
                                "Skipped": r["skipped"], "Duration": fmt_duration(r["duration"]), "Engine": r["engine"], "Started": r["started_at"]} for r in runs]), hide_index=True)
    ids = [r["id"] for r in runs]
    default = st.session_state.pop("history_open", None)
    tab_open, tab_cmp, tab_del = st.tabs(["Open a run", "Compare two runs", "Delete a run"])
    with tab_open:
        sel = st.selectbox("Run", ids, index=ids.index(default) if default in ids else 0, format_func=lambda i: f"#{i} · {next(r['url'] for r in runs if r['id'] == i)}", key="h_open")
        show_run_detail(sel, "hist")
    with tab_cmp:
        if len(ids) < 2:
            st.info("You need at least two runs to compare.")
        else:
            c1, c2 = st.columns(2)
            a = c1.selectbox("Baseline run (older)", ids, index=min(1, len(ids) - 1), key="h_a")
            b = c2.selectbox("Compare with (newer)", ids, index=0, key="h_b")
            if a == b:
                st.warning("Pick two different runs.")
            else:
                df = pd.DataFrame(db.compare_runs(a, b))
                st.write(f"Regressions: **{(df['change'] == 'regression').sum()}** · Fixed: **{(df['change'] == 'fixed').sum()}** · Unchanged: **{(df['change'] == 'same').sum()}**")
                st.dataframe(df, hide_index=True)
    with tab_del:
        d = st.selectbox("Run to delete", ids, key="h_del")
        sure = st.checkbox(f"Yes, permanently delete run #{d}, its bugs and screenshots", key=f"h_sure_{d}")
        if st.button("Delete run", disabled=not sure, key="h_delbtn"):
            db.delete_run(d)
            if st.session_state.get("last_run_id") == d:
                st.session_state.pop("last_run_id")
            st.success(f"Run #{d} deleted.")
            st.rerun()
