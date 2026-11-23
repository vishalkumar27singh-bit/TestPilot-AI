"""REAL browser tests: they launch Chrome through Selenium and drive the bundled sample site. They are SKIPPED (with the reason)
when Selenium or Chrome is unavailable, so they can never pass without a real browser.

Run:  pytest tests/test_browser_real.py -v      or      python selfcheck_browser.py
"""
import struct
import tempfile
import unittest
from pathlib import Path

import core.test_executor as ex
import database.db_manager as dbm
import utils.screenshot_manager as sm
from core.models import TestFailure, TestSkipped
from core.test_executor import run_website_test
from core.website_analyzer import analyze_website
from testing import ui_tests
from testing.context import BrowserSession, BrowserUnavailable, RunContext
from tests.helpers import LOOPBACK, base_url, temp_db
from utils.logger import RunLog

_state: dict = {}
CFG = {"engine": "selenium", "allow_loopback": True, "use_ai": False, "request_timeout": 5, "render_wait": 6}


def need_browser():
    """Skip (never pass) when a real browser cannot be started."""
    if "ok" not in _state:
        try:
            s = BrowserSession(True, 30).start()
            _state.update(ok=True, info=dict(s.info))
            s.close()
        except BrowserUnavailable as e:
            _state.update(ok=False, why=str(e))
    if not _state["ok"]:
        raise unittest.SkipTest("real browser unavailable: " + _state["why"])


def run_real(page, **cfg):
    """Full workflow in real Chrome. Screenshots go to a temp folder so real history is untouched."""
    need_browser()
    shots = Path(tempfile.mkdtemp(prefix="tp_shots_"))
    old = (sm.SCREENSHOT_DIR, dbm.SCREENSHOT_DIR)
    sm.SCREENSHOT_DIR = dbm.SCREENSHOT_DIR = shots
    try:
        db = temp_db()
        rid = run_website_test(f"{base_url()}/{page}", {**CFG, **cfg}, db=db)
    finally:
        sm.SCREENSHOT_DIR, dbm.SCREENSHOT_DIR = old
    run = db.get_run(rid)
    assert run["engine"].startswith("Selenium"), "Chrome was available but the run fell back to static HTTP:\n" + run["log_text"][-1500:]
    return db, rid, {t["name"]: t for t in db.get_test_cases(rid)}


def png_size(path):
    b = Path(path).read_bytes()
    assert b[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", b[16:24])


def live(page):
    need_browser()
    b = BrowserSession(True, 30).start()
    url = f"{base_url()}/{page}"
    b.open(url, 6)
    prof = analyze_website(url, html=b.driver.page_source, final_url=b.driver.current_url, **LOOPBACK)
    prof.status_code = 200
    return RunContext(profile=prof, settings={**LOOPBACK, "request_timeout": 5, "render_wait": 6}, log=RunLog(), browser=b), b


def test_real_chrome_launches_and_versions_are_logged():
    db, rid, t = run_real("index.html")
    log = db.get_run(rid)["log_text"]
    assert "Real browser running: chrome" in log.lower() or "Real browser running: " in log
    assert _state["info"].get("version"), "browser version not reported"


def test_real_title_and_page_load_pass_in_the_browser():
    _, _, t = run_real("index.html")
    for name in ("Website loads", "Page title present"):
        assert t[name]["status"] == "PASS" and t[name]["engine"] == "Browser", (name, t[name]["actual"])
    assert "Acme" in t["Page title present"]["actual"]


def test_real_javascript_is_executed_and_static_http_cannot_see_it():
    db, rid, t = run_real("jsrender.html")
    prof = db.get_run(rid)["profile_json"]
    assert "JS button" in prof and "Rendered by JavaScript" in prof  # only exists after scripts ran in Chrome
    s_db = temp_db()
    s_rid = run_website_test(f"{base_url()}/jsrender.html", {**CFG, "engine": "static"}, db=s_db)
    assert "JS button" not in s_db.get_run(s_rid)["profile_json"]  # the static engine sees an empty shell


def test_real_viewports_resize_and_layout_is_measured():
    _, _, t = run_real("index.html")  # has a fixed 1200px banner
    assert t["Responsive layout: desktop (1366x768)"]["status"] == "PASS"
    for vp in ("tablet (768x1024)", "mobile (390x844)"):
        r = t[f"Responsive layout: {vp}"]
        assert r["status"] == "FAIL" and r["engine"] == "Browser" and "Horizontal overflow" in r["actual"], r["actual"]
    _, _, ok = run_real("responsive_ok.html")
    for vp in ("desktop (1366x768)", "tablet (768x1024)", "mobile (390x844)"):
        assert ok[f"Responsive layout: {vp}"]["status"] == "PASS", ok[f"Responsive layout: {vp}"]["actual"]


def test_real_screenshots_are_real_pngs_taken_at_different_viewport_sizes():
    db, rid, t = run_real("responsive_ok.html")
    shots = {s["label"]: s["path"] for s in db.list_screenshots(rid)}
    assert {"loaded", "desktop_1366x768", "tablet_768x1024", "mobile_390x844"} <= set(shots), sorted(shots)
    w = {k: png_size(shots[k])[0] for k in ("desktop_1366x768", "tablet_768x1024", "mobile_390x844")}
    assert w["mobile_390x844"] < w["tablet_768x1024"] < w["desktop_1366x768"], w  # proves the viewport was really resized
    assert all(Path(p).stat().st_size > 1000 for p in shots.values())


def test_real_failed_test_keeps_screenshot_of_the_failing_viewport():
    db, rid, t = run_real("index.html")
    mobile, desktop = t["Responsive layout: mobile (390x844)"], t["Responsive layout: desktop (1366x768)"]
    assert mobile["status"] == "FAIL" and mobile["screenshot"]
    assert png_size(mobile["screenshot"])[0] < png_size(desktop["screenshot"])[0]
    assert any(b["test_id"] == mobile["test_id"] and b["screenshot"] for b in db.list_bugs(rid))


def test_real_console_errors_are_detected():
    _, _, t = run_real("jserror.html")
    r = t["No browser console errors"]
    assert r["status"] == "FAIL" and r["engine"] == "Browser" and ("uncaught sample error" in r["actual"] or "sample console error" in r["actual"]), r["actual"]
    _, _, clean = run_real("responsive_ok.html")
    assert clean["No browser console errors"]["status"] == "PASS"
    _, _, broken_img = run_real("index.html")  # index.html references a missing image: Chrome logs a 404 as a SEVERE console entry
    assert broken_img["No browser console errors"]["status"] == "FAIL"


def test_real_safe_clicks_never_press_destructive_buttons():
    ctx, b = live("clicks.html")
    try:
        msg = ui_tests.click_buttons(ctx, {})
        assert "Show details" in msg and "not clicked for safety" in msg
        assert b.driver.execute_script("return localStorage.getItem('danger_clicked')") is None  # 'Delete account' was NOT clicked
        assert b.driver.title != "DANGER CLICKED"
    finally:
        b.close()


def test_real_click_that_throws_is_reported():
    ctx, b = live("clickerror.html")
    try:
        try:
            ui_tests.click_buttons(ctx, {})
            raise AssertionError("a button whose handler throws must FAIL")
        except TestFailure as e:
            assert "Load more" in str(e) and "console error" in str(e)
    finally:
        b.close()


def test_real_navigation_links_are_clicked():
    ctx, b = live("clicks.html")
    try:
        msg = ui_tests.navigation_links(ctx, {})
        assert "clicked 3 internal link" in msg and b.driver.current_url.endswith("clicks.html")
    finally:
        b.close()


def test_real_search_form_is_submitted_in_the_browser():
    _, _, t = run_real("index.html")
    r = t["Search box works"]
    assert r["status"] == "PASS" and r["engine"] == "Browser" and "search.html" in r["actual"], r["actual"]


def test_real_run_labels_browser_and_static_checks_separately():
    _, _, t = run_real("index.html")
    assert t["Website loads"]["engine"] == "Browser" and t["No broken links"]["engine"] == "Static/HTTP"
    assert t["Viewport meta tag present (static HTML check)"]["engine"] == "Static/HTTP"
