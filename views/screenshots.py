from __future__ import annotations

import streamlit as st

from views.common import empty_state, get_db, page_header, render_screenshots


def render():
    page_header("Screenshots", "Evidence captured during Selenium runs, including automatic captures for failed tests.")
    db = get_db()
    runs = [r for r in db.list_runs("ui") if db.list_screenshots(r["id"])]
    if not runs:
        return empty_state("No screenshots yet. They are captured when tests run with the Selenium (Chrome) engine.")
    rid = st.selectbox("Run", [r["id"] for r in runs], format_func=lambda i: f"#{i} · {next(r['url'] for r in runs if r['id'] == i)}", key="ss_run")
    render_screenshots(db.list_screenshots(rid), f"gal_{rid}")
