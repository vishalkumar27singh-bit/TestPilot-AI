"""Browser-engine LOGIC tests using a SIMULATED driver (tests/fakebrowser.py). These do NOT prove Chrome works: they prove that
TestPilot's browser code paths behave correctly (labels, SKIPPED reasons, safe-click rules, viewport handling, launch options...).
Real-Chrome verification lives in tests/test_browser_real.py and selfcheck_browser.py."""
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

import core.test_executor as ex
import testing.browser_config as bc
from core.models import SiteProfile, TestSkipped
from core.test_executor import run_website_test
from core.website_analyzer import analyze_website
from database.db_manager import DB
from reports.report_generator import generate
from testing.context import BrowserSession, BrowserUnavailable, RunContext
from testing.registry import engine_label
from testing.responsive_tests import responsive
from tests.fakebrowser import FakeDOMDriver, SimBrowserSession, selenium_stub
from tests.helpers import LOOPBACK, ROOT, base_url, temp_db
from utils.helpers import fallback_notice
from utils.logger import RunLog

CFG = {"engine": "selenium", "allow_loopback": True, "use_ai": False, "request_timeout": 2, "render_wait": 1}


def run_sim(page, **drv):
    """Run the whole workflow with the executor wired to the simulated browser. Returns (db, run_id, {name: test}, driver)."""
    SimBrowserSession.drv_kwargs, SimBrowserSession.created = drv, []
    orig, db = ex.BrowserSession, temp_db()
    ex.BrowserSession = SimBrowserSession
    try:
        with selenium_stub():
            rid = run_website_test(f"{base_url()}/{page}", CFG, db=db)
    finally:
        ex.BrowserSession = orig
    return db, rid, {t["name"]: t for t in db.get_test_cases(rid)}, SimBrowserSession.created[0]


def png_width(path):
    b = Path(path).read_bytes()
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">I", b[16:20])[0]


# ---------------- full workflow on the browser path
def test_browser_path_runs_real_handlers_and_labels_engine_honestly():
    db, rid, t, drv = run_sim("index.html")
    run = db.get_run(rid)
    assert run["engine"] == "Selenium (Chrome)" and "Real browser running" in run["log_text"]
    for name in ("Website loads", "Page title present", "Navigation links work", "Input fields accept text", "Search box works", "No browser console errors"):
        assert t[name]["status"] == "PASS" and t[name]["engine"] == "Browser", (name, t[name]["actual"])
    for name in ("HTTP availability", "No broken links", "Images have alt text", "Viewport meta tag present (static HTML check)"):
        assert t[name]["engine"] == "Static/HTTP", name  # static analysis is never labelled Browser, even in a browser run
    assert t["Viewport meta tag present (static HTML check)"]["status"] == "PASS"
    assert t["No broken links"]["status"] == "FAIL" and t["Buttons are usable"]["status"] == "FAIL" and "accessible name" in t["Buttons are usable"]["actual"]
    assert t["Images render"]["status"] == "FAIL" and t["Images render"]["engine"] == "Browser"
    db.delete_run(rid)


def test_viewports_are_really_resized_measured_and_restored():
    db, rid, t, drv = run_sim("index.html")
    assert t["Responsive layout: desktop (1366x768)"]["status"] == "PASS"
    for vp in ("tablet (768x1024)", "mobile (390x844)"):
        r = t[f"Responsive layout: {vp}"]
        assert r["status"] == "FAIL" and r["engine"] == "Browser" and "Horizontal overflow" in r["actual"] and "1200px" in r["actual"]
    assert "Emulation.setDeviceMetricsOverride" in drv.cdp_calls and drv.cdp_calls.count("Emulation.clearDeviceMetricsOverride") >= 3
    assert (drv.vw, drv.vh) == (1366, 900)  # always restored
    db.delete_run(rid)


def test_responsive_page_passes_all_three_viewports():
    db, rid, t, _ = run_sim("responsive_ok.html")
    assert [t[f"Responsive layout: {v}"]["status"] for v in ("desktop (1366x768)", "tablet (768x1024)", "mobile (390x844)")] == ["PASS"] * 3
    db.delete_run(rid)


def test_screenshots_are_saved_in_project_folder_and_recorded():
    db, rid, t, _ = run_sim("index.html")
    shots = db.list_screenshots(rid)
    assert len(shots) >= 6
    for s in shots:
        p = Path(s["path"])
        assert p.exists() and p.stat().st_size > 0 and ROOT / "screenshots" in p.parents and png_width(p) > 0
    labels = {s["label"] for s in shots}
    assert {"loaded", "desktop_1366x768", "mobile_390x844"} <= labels
    mobile = next(s for s in shots if s["label"] == "mobile_390x844")
    assert png_width(mobile["path"]) == 390  # taken AFTER the resize (plumbing check; the simulated PNG mirrors the viewport)
    assert t["Responsive layout: mobile (390x844)"]["screenshot"] == mobile["path"]  # failed test links its evidence
    db.delete_run(rid)
    assert not Path(mobile["path"]).exists()  # deleting a run removes its screenshots


def test_console_errors_detected_and_bug_filed():
    db, rid, t, _ = run_sim("jserror.html")
    assert t["No browser console errors"]["status"] == "FAIL" and "console error" in t["No browser console errors"]["actual"]
    assert any(b["test_id"] == t["No browser console errors"]["test_id"] for b in db.list_bugs(rid))
    db.delete_run(rid)


# ---------------- safe clicking
def test_safe_buttons_clicked_destructive_never():
    db, rid, t, drv = run_sim("clicks.html")
    r = t["Safe buttons can be clicked"]
    assert r["status"] == "PASS" and r["engine"] == "Browser" and "Show details" in r["actual"] and "1 button(s) were not clicked for safety" in r["actual"]
    assert "Show details" in drv.clicked and "Delete account" not in drv.clicked
    assert t["Navigation links work"]["status"] == "PASS" and {"Home", "About"} <= set(drv.clicked)  # nav links really clicked
    db.delete_run(rid)


def test_click_that_breaks_the_page_is_reported():
    db, rid, t, drv = run_sim("clickerror.html")
    r = t["Safe buttons can be clicked"]
    assert r["status"] == "FAIL" and "Load more" in r["actual"] and "console error" in r["actual"] and drv.clicked == ["Load more"]
    db.delete_run(rid)


def test_nothing_safe_to_click_is_skipped_with_reason_not_passed():
    db, rid, t, drv = run_sim("index.html")  # only: a form button, 'Buy now' (denied) and an unnamed icon button
    r = t["Safe buttons can be clicked"]
    assert r["status"] == "SKIPPED" and r["engine"] == "Not run" and "never clicked" in r["actual"]
    assert "Buy now" not in drv.clicked and "Search" not in drv.clicked
    db.delete_run(rid)


# ---------------- no browser: static fallback must be honest
def test_without_browser_browser_tests_are_skipped_with_real_reason_never_passed():
    class NoChrome:
        def __init__(self, *a, **k): pass
        def start(self): raise BrowserUnavailable("simulated: Chrome is not installed.")
        def close(self): pass
    orig, db = ex.BrowserSession, temp_db()
    ex.BrowserSession = NoChrome
    try:
        rid = run_website_test(f"{base_url()}/index.html", {**CFG, "engine": "auto"}, db=db)
    finally:
        ex.BrowserSession = orig
    t, run = {x["name"]: x for x in db.get_test_cases(rid)}, db.get_run(rid)
    assert run["engine"].startswith("static") and db.list_screenshots(rid) == []
    for name in ("Responsive layout: desktop (1366x768)", "Responsive layout: mobile (390x844)", "No browser console errors", "Safe buttons can be clicked"):
        assert t[name]["status"] == "SKIPPED" and t[name]["engine"] == "Not run", name
        assert "real browser" in t[name]["actual"] and "simulated: Chrome is not installed." in t[name]["actual"], t[name]["actual"]
    assert all(x["engine"] != "Browser" for x in t.values())  # nothing may claim to be a browser test
    assert t["Viewport meta tag present (static HTML check)"]["engine"] == "Static/HTTP"
    assert "static HTTP engine" in fallback_notice(run)


def test_engine_label_rules():
    assert engine_label("responsive", "PASS", True) == "Browser" and engine_label("responsive", "SKIPPED", False) == "Not run"
    assert engine_label("broken_links", "PASS", True) == "Static/HTTP" and engine_label("image_alt_text", "FAIL", True) == "Static/HTTP"
    assert engine_label("page_title", "PASS", False) == "Static/HTTP" and engine_label("page_title", "PASS", True) == "Browser"
    assert engine_label("console_errors", "PASS", False) == "Static/HTTP"  # cannot happen (skipped) but must never say Browser


# ---------------- viewport edge cases
def _ctx(**drv):
    SimBrowserSession.drv_kwargs, SimBrowserSession.created = drv, []
    b = SimBrowserSession(True, 30).start()
    url = f"{base_url()}/responsive_ok.html"
    b.driver.get(url)
    prof = analyze_website(url, html=b.driver.page_source, final_url=b.driver.current_url, **LOOPBACK)
    return RunContext(profile=prof, settings={**LOOPBACK, "request_timeout": 3, "render_wait": 1}, log=RunLog(), browser=b), b.driver


def test_window_resize_fallback_and_impossible_viewport_is_skipped_not_passed():
    ctx, drv = _ctx(cdp_supported=False, min_window_width=500)  # a browser that refuses narrow windows and has no DevTools emulation
    with selenium_stub():
        assert "window resize" in responsive(ctx, {"viewport": "tablet"})
        try:
            responsive(ctx, {"viewport": "mobile"})
            raise AssertionError("an impossible 390px viewport must not PASS")
        except TestSkipped as e:
            assert "390" in str(e) and "500" in str(e)
    assert drv.vw == 1366


def test_browser_only_check_without_browser_raises_skip_with_reason():
    prof = SiteProfile(url="http://x.test/")
    ctx = RunContext(profile=prof, settings={}, log=RunLog(), browser=None, browser_reason="Selenium is missing.")
    try:
        responsive(ctx, {"viewport": "mobile"})
        raise AssertionError("expected skip")
    except TestSkipped as e:
        assert "real browser" in str(e) and "Selenium is missing." in str(e)


# ---------------- launching Chrome (options per platform)
def _start(cfg_factory, chrome=None, headless=True):
    orig = bc.resolve_browser_config
    bc.resolve_browser_config = cfg_factory
    try:
        with selenium_stub(chrome) as launched:
            s = BrowserSession(headless, 30).start()
            return s, launched
    finally:
        bc.resolve_browser_config = orig


REAL = bc.resolve_browser_config
MAC = lambda getenv=lambda k: None: (lambda headless: REAL(headless, "darwin", getenv, lambda n: None, lambda p: True, True))
LINUX = lambda headless: REAL(headless, "linux", lambda k: None, lambda n: f"/usr/bin/{n}" if n in ("chromium", "chromedriver") else None, lambda p: False, False)


def test_launch_options_macos_apple_silicon_uses_selenium_manager():
    s, l = _start(MAC())
    o = l["options"]
    assert o.binary_location is None and l["service"].executable_path is None  # Chrome + driver auto-detected / auto-downloaded
    assert "--headless=new" in o.arguments and "--no-sandbox" not in o.arguments and o.caps["goog:loggingPrefs"] == {"browser": "ALL"}
    assert s.info["version"] == "0.0-SIMULATED" and s.info["driver_version"] == "0.0-SIMULATED"
    _, l2 = _start(MAC(), headless=False)
    assert "--headless=new" not in l2["options"].arguments  # visible window for local debugging


def test_launch_options_macos_chrome_binary_override_is_honoured():
    _, l = _start(MAC({"CHROME_BINARY": "/custom/Chrome"}.get))
    assert l["options"].binary_location == "/custom/Chrome"


def test_launch_options_linux_cloud():
    _, l = _start(LINUX)
    assert l["options"].binary_location == "/usr/bin/chromium" and l["service"].executable_path == "/usr/bin/chromedriver"
    assert {"--no-sandbox", "--disable-dev-shm-usage", "--headless=new"} <= set(l["options"].arguments)


def test_launch_failure_and_missing_selenium_give_actionable_errors():
    def boom(service, options): raise RuntimeError("session not created: This version of ChromeDriver only supports Chrome version 99")
    try:
        _start(LINUX, chrome=boom)
        raise AssertionError("expected BrowserUnavailable")
    except BrowserUnavailable as e:
        assert "Could not start Chrome" in str(e) and "ChromeDriver only supports" in str(e) and "versions must match" in str(e)
    old = sys.modules.get("selenium", "__absent__")
    sys.modules["selenium"] = None
    try:
        BrowserSession().start()
        raise AssertionError("expected BrowserUnavailable")
    except BrowserUnavailable as e:
        assert sys.executable in str(e) and "pip install -r requirements.txt" in str(e) and "python -m streamlit run app.py" in str(e)
    finally:
        sys.modules.pop("selenium", None) if old == "__absent__" else sys.modules.__setitem__("selenium", old)


def test_diagnose_reports_environment():
    with selenium_stub():
        d = bc.diagnose()
    assert d["selenium"] == "4.99.0-stub" and d["python"] == sys.executable and d["arch"] and "install_command" in d
    assert bc.diagnose()["install_command"].startswith(sys.executable)


# ---------------- storage / reports
def test_old_database_is_migrated_and_stores_engine():
    path = tempfile.mktemp(suffix=".db")
    old_schema = (ROOT / "database" / "schema.sql").read_text().replace(", engine TEXT", "")
    con = sqlite3.connect(path); con.executescript(old_schema)
    con.execute("INSERT INTO runs(url,started_at) VALUES('http://old.test','2026-01-01 00:00:00')")
    con.execute("INSERT INTO test_cases(run_id,test_id,name,status) VALUES(1,'UI-001','legacy test','PASS')"); con.commit(); con.close()
    db = DB(path)
    assert db.get_test_cases(1)[0]["name"] == "legacy test"  # existing history survives
    from core.models import TestCase
    tc = TestCase("UI-002", "new", "c", "o", ["s"], "e", "page_load", status="PASS", actual="ok", engine="Browser")
    db.add_test_cases(1, [tc])
    assert db.get_test_cases(1)[-1]["engine"] == "Browser"


def test_reports_show_engine_per_test():
    db, rid, t, _ = run_sim("responsive_ok.html")
    import csv, io
    rows = list(csv.DictReader(io.StringIO(generate(rid, "csv", db)[0].decode("utf-8-sig"))))
    assert {r["engine"] for r in rows} >= {"Browser", "Static/HTTP"}
    assert b"<th>Engine</th>" in generate(rid, "html", db)[0] and generate(rid, "pdf", db)[0].startswith(b"%PDF")
    db.delete_run(rid)
