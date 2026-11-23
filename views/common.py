"""Shared UI helpers: CSS, cards, charts, run detail panel."""
from __future__ import annotations

from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from database.db_manager import DB
from reports.report_generator import FORMATS, generate
from utils.helpers import fallback_notice, fmt_duration

try:
    import plotly.graph_objects as go
except ImportError:  # charts degrade gracefully
    go = None

PALETTE = {"PASS": "#1F9D6B", "FAIL": "#D9482B", "SKIPPED": "#8A8F98"}
SEV_COLORS = {"LOW": "#9AA5BA", "MEDIUM": "#E0A800", "HIGH": "#E8743B", "CRITICAL": "#B3261E"}


@st.cache_resource
def get_db() -> DB:
    return DB()


def load_css():
    css = (Path(__file__).resolve().parent.parent / "assets" / "styles.css").read_text()
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def page_header(title: str, subtitle: str = ""):
    st.markdown(f"<div class='page-head'><h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div>", unsafe_allow_html=True)


def metric_cards(items: list[tuple[str, object, str, str]]):
    """items: (label, value, sub, tone)"""
    html = "".join(f"<div class='metric {tone}'><div class='l'>{escape(l)}</div><div class='v'>{escape(str(v))}</div><div class='s'>{escape(s)}</div></div>"
                   for l, v, s, tone in items)
    st.markdown(f"<div class='metrics'>{html}</div>", unsafe_allow_html=True)


def pill(text: str) -> str:
    return f"<span class='pill {escape(str(text).lower())}'>{escape(str(text))}</span>"


def empty_state(msg: str):
    st.markdown(f"<div class='empty'>{escape(msg)}</div>", unsafe_allow_html=True)


def goto(page: str):
    st.session_state["pending_nav"] = page
    st.rerun()


# ---------- charts
def _layout(fig, height=280):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=36, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="IBM Plex Sans, sans-serif", size=12, color="#111827"), legend=dict(orientation="h", y=-0.15))
    return fig


def chart_pass_fail(passed: int, failed: int, skipped: int, title="Pass vs fail"):
    if go is None:
        return st.info("Install plotly to see charts: pip install plotly")
    if passed + failed + skipped == 0:
        return empty_state("No results yet")
    fig = go.Figure(go.Pie(labels=["Passed", "Failed", "Skipped"], values=[passed, failed, skipped], hole=.62, sort=False,
                           marker=dict(colors=[PALETTE["PASS"], PALETTE["FAIL"], PALETTE["SKIPPED"]]), textinfo="value"))
    fig.update_layout(title=title)
    st.plotly_chart(_layout(fig), key=f"pf_{title}_{passed}_{failed}")


def chart_history(runs: list[dict]):
    if go is None:
        return st.info("Install plotly to see charts: pip install plotly")
    runs = [r for r in runs if r["status"] == "COMPLETED"][:25][::-1]
    if not runs:
        return empty_state("No completed runs yet")
    x = [f"#{r['id']}" for r in runs]
    fig = go.Figure()
    for key, name, color in (("passed", "Passed", PALETTE["PASS"]), ("failed", "Failed", PALETTE["FAIL"]), ("skipped", "Skipped", PALETTE["SKIPPED"])):
        fig.add_bar(x=x, y=[r[key] for r in runs], name=name, marker_color=color)
    fig.update_layout(barmode="stack", title="Execution history (latest 25 runs)")
    st.plotly_chart(_layout(fig), key="hist_chart")


def chart_counts(values: dict, title: str, colors: dict | None = None, key: str = ""):
    if go is None:
        return st.info("Install plotly to see charts: pip install plotly")
    if not values:
        return empty_state("No data yet")
    fig = go.Figure(go.Bar(x=list(values), y=list(values.values()), marker_color=[(colors or {}).get(k, "#2F4BFF") for k in values]))
    fig.update_layout(title=title)
    st.plotly_chart(_layout(fig), key=f"cnt_{key or title}")


# ---------- results
def tests_dataframe(tests: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"ID": t["test_id"], "Test": t["name"], "Category": t["category"], "Status": t["status"], "Engine": t.get("engine") or "", "Severity": t["severity"],
                          "Time": fmt_duration(t["duration"]), "Actual result": t["actual"]} for t in tests])


def _style_status(df: pd.DataFrame):
    colors = {"PASS": "#D6F0E4", "FAIL": "#F8D9D2", "SKIPPED": "#E6E7EA"}
    return df.style.map(lambda v: f"background-color:{colors.get(v, '')}", subset=["Status"])


def render_failure(t: dict):
    extra = f"<p><b>AI explanation:</b> {escape(t['ai_explanation'])}</p>" if t.get("ai_explanation") else ""
    st.markdown(f"<div class='failcard'><h4>{escape(t['test_id'])} · {escape(t['name'])} &nbsp;{pill(t['severity'])}</h4>"
                f"<p><b>Failed step:</b> {escape(t['failed_step'] or '—')}</p><p><b>Error details:</b> {escape(t['error'] or t['actual'])}</p>"
                f"<p><b>Probable cause:</b> {escape(t['probable_cause'])}</p><p><b>Suggested fix:</b> {escape(t['suggested_fix'])}</p>{extra}</div>",
                unsafe_allow_html=True)
    if t.get("screenshot") and Path(t["screenshot"]).exists():
        st.image(t["screenshot"], caption=f"Screenshot — {t['test_id']}", width=520)


def show_run_detail(run_id: int, key: str = "rd"):
    db = get_db()
    run = db.get_run(run_id)
    if not run:
        return empty_state("Run not found.")
    tests = db.get_test_cases(run_id)
    executed = run["passed"] + run["failed"]
    rate = f"{100 * run['passed'] / executed:.0f}%" if executed else "—"
    st.markdown(f"<div class='card target'><div><div class='k'>Run</div><div class='v'>#{run['id']} · {escape(run['kind'].upper())}</div></div>"
                f"<div><div class='k'>Target</div><div class='v'>{escape(run['final_url'] or run['url'])}</div></div>"
                f"<div><div class='k'>Engine</div><div class='v'>{escape(run['engine'] or '—')}</div></div>"
                f"<div><div class='k'>Mode</div><div class='v'>{escape(run['ai_mode'] or '—')}</div></div>"
                f"<div><div class='k'>Started</div><div class='v'>{escape(run['started_at'])}</div></div></div>", unsafe_allow_html=True)
    metric_cards([("Total tests", run["total"], "", "accent"), ("Passed", run["passed"], "", "pass"), ("Failed", run["failed"], "", "fail"),
                  ("Skipped", run["skipped"], "", "skip"), ("Pass rate", rate, "of executed tests", "accent"), ("Duration", fmt_duration(run["duration"]), "", "accent")])
    notice = fallback_notice(run)
    if notice:
        st.warning(notice)
    if run.get("status") == "ERROR":
        st.error(f"This run ended with an error: {run.get('error') or 'unknown'}")
    if run.get("ai_summary"):
        with st.expander("Summary & recommendations", expanded=True):
            st.text(run["ai_summary"])
    t1, t2, t3, t4, t5, t6 = st.tabs(["Results", "Failures", "Bug reports", "Screenshots", "Execution log", "Download"])
    with t1:
        if tests:
            st.dataframe(_style_status(tests_dataframe(tests)), hide_index=True)
            with st.expander("Test case details (steps, expected, actual)"):
                for t in tests:
                    st.markdown(f"**{t['test_id']} · {t['name']}** — {t['status']} · {t['severity']} · source: {t['source']}")
                    st.markdown(f"*Objective:* {t['objective']}  \n*Steps:* " + " → ".join(t["steps"]) + f"  \n*Expected:* {t['expected']}  \n*Actual:* {t['actual']}")
        else:
            empty_state("No test cases recorded.")
    with t2:
        fails = [t for t in tests if t["status"] == "FAIL"]
        for t in fails:
            render_failure(t)
        if not fails:
            st.success("No failed tests in this run.")
    with t3:
        bugs = db.list_bugs(run_id)
        for b in bugs:
            render_bug(b)
        if not bugs:
            empty_state("No bugs: nothing failed in this run.")
    with t4:
        render_screenshots(db.list_screenshots(run_id), f"{key}_{run_id}")
    with t5:
        st.code(run.get("log_text") or "No log stored for this run.", language="text")
    with t6:
        cols = st.columns(4)
        for col, fmt in zip(cols, FORMATS):
            try:
                data, mime, name = generate(run_id, fmt, db)
                col.download_button(f"⬇ {fmt.upper()} report", data, name, mime, key=f"dl_{key}_{run_id}_{fmt}")
            except Exception as e:  # one broken format must not hide the others
                col.error(f"{fmt.upper()} failed: {type(e).__name__}")


def render_bug(b: dict):
    with st.expander(f"{b['bug_id']} · {b['title']}  [{b['severity']}]  — {b['status']}"):
        st.markdown(f"**Test run:** #{b['run_id']} · **Created:** {b['created_at']}  \n**Description:** {b['description']}")
        st.markdown("**Steps to reproduce**")
        st.text(b["steps_to_reproduce"])
        st.markdown(f"**Expected behavior:** {b['expected']}  \n**Actual behavior:** {b['actual']}  \n**Suggested solution:** {b['suggested_solution']}")
        if b.get("screenshot") and Path(b["screenshot"]).exists():
            st.image(b["screenshot"], width=480)


def render_screenshots(shots: list[dict], key: str):
    shots = [s for s in shots if Path(s["path"]).exists()]
    if not shots:
        return empty_state("No screenshots for this run. Screenshots need the Selenium (Chrome) engine; the static HTTP engine cannot capture them.")
    cols = st.columns(3)
    for i, s in enumerate(shots):
        with cols[i % 3]:
            st.image(s["path"], caption=f"{s['test_id']} · {s['label']}")
            st.download_button("Download", Path(s["path"]).read_bytes(), Path(s["path"]).name, "image/png", key=f"ss_{key}_{s['id']}")
