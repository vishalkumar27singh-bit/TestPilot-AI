from __future__ import annotations

import streamlit as st

from utils.helpers import fmt_duration
from views.common import (SEV_COLORS, chart_counts, chart_history, chart_pass_fail, empty_state, get_db, goto, metric_cards)
from views.new_run import run_panel


def render():
    st.markdown("<div class='hero'><h1>Point it at a website. Get a bug report back.</h1>"
                "<p>TestPilot AI analyzes the page, plans tests, drives a real browser, captures evidence for every failure and explains what to fix.</p></div>",
                unsafe_allow_html=True)
    db = get_db()
    run_panel("ov")
    st.markdown("### Overview")
    s = db.stats()
    metric_cards([("Test runs", s["runs"], "completed", "accent"), ("Test cases", s["total"], "executed", "accent"), ("Passed", s["passed"], "", "pass"),
                  ("Failed", s["failed"], "", "fail"), ("Skipped", s["skipped"], "", "skip"), ("Pass rate", f"{s['pass_rate']}%", "passed / (passed+failed)", "accent"),
                  ("Avg. run time", fmt_duration(s["avg_duration"]), "", "accent")])
    runs = db.list_runs()
    tests = db.all_test_cases()
    c1, c2 = st.columns(2)
    with c1:
        chart_pass_fail(s["passed"], s["failed"], s["skipped"])
    with c2:
        chart_history(runs)
    c3, c4 = st.columns(2)
    with c3:
        sev: dict = {}
        for t in tests:
            if t["status"] == "FAIL":
                sev[t["severity"]] = sev.get(t["severity"], 0) + 1
        chart_counts({k: sev[k] for k in ("LOW", "MEDIUM", "HIGH", "CRITICAL") if k in sev}, "Failures by severity", SEV_COLORS, "sev")
    with c4:
        cat: dict = {}
        for t in tests:
            cat[t["category"]] = cat.get(t["category"], 0) + 1
        chart_counts(dict(sorted(cat.items(), key=lambda kv: -kv[1])), "Test category distribution", key="cat")
    st.markdown("### Recent testing sessions")
    if not runs:
        empty_state("No sessions yet. Run your first test above, or start the bundled sample site (see Settings → Sample site).")
    for r in runs[:5]:
        a, b, c = st.columns([5, 3, 1])
        a.markdown(f"**#{r['id']}** · {r['url']}  \n<small>{r['started_at']} · {r['kind'].upper()}</small>", unsafe_allow_html=True)
        b.markdown(f"✅ {r['passed']} &nbsp; ❌ {r['failed']} &nbsp; ⏭ {r['skipped']}")
        if c.button("Open", key=f"ov_open_{r['id']}"):
            st.session_state["history_open"] = r["id"]
            goto("History")
