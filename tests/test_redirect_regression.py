"""Regression tests for 'Unavailable: Too many redirects (>5).'  (arbitrary sites, nothing site-specific).

Root cause: redirect-following failures were converted to ok=False ('site unavailable') and the run was aborted, even though
the server had answered; v1.0.1 additionally allowed only 5 hops and had no cookie jar."""
import inspect
import sys
import types

import core.test_executor as ex
from core.safety import RedirectError, check_availability, safe_request
from core.test_executor import run_website_test, run_websites
from tests.helpers import LOOPBACK, base_url, temp_db
from tests.multisite import site

CFG = {"engine": "static", "allow_loopback": True, "use_ai": False, "request_timeout": 2}
LANDING = ('<!doctype html><html lang="en"><head><title>Landing page after redirects</title></head><body><p>'
           + "Ordinary readable content for the landing page. " * 8 + "</p></body></html>")


# ---- the original failure
def test_old_5_hop_limit_is_gone():
    assert inspect.signature(safe_request).parameters["max_redirects"].default >= 10
    src = (inspect.getsource(ex) + inspect.getsource(sys.modules["core.safety"]))
    assert "(>5)" not in src and "max_redirects=5" not in src


def test_ordinary_long_redirect_chain_is_available():
    av = check_availability(site("long_chain") + "/h1", **LOOPBACK)
    assert av["ok"] and av["status"] == 200 and av["final_url"].endswith("/end") and len(av["redirects"]) == 8
    assert av["error"] == "" and not av["redirect_unresolved"]


def test_cookie_handshake_redirects_are_available():
    av = check_availability(site("cookie") + "/", **LOOPBACK)
    assert av["ok"] and av["status"] == 200 and av["final_url"].endswith("/home")


def test_header_dependent_redirect_loop_is_resolved_by_retry():
    av = check_availability(site("accept_gate") + "/", **LOOPBACK)
    assert av["ok"] and av["status"] == 200 and not av["redirect_unresolved"]
    assert any("browser-like" in n for n in av["notes"])


# ---- the actual root-cause rule: redirect trouble != unavailable
def test_unresolvable_redirects_mean_reachable_not_unavailable():
    base = site("endless")
    av = check_availability(base + "/", **LOOPBACK)
    assert av["ok"] is True and av["redirect_unresolved"] is True and av["error"] == ""
    assert av["status"] == 303 and av["final_url"].startswith(base) and len(av["redirects"]) >= 10
    assert any("does not mean the site is down" in n for n in av["notes"])


def test_run_continues_without_a_browser_and_never_fakes_results():
    db = temp_db()
    rid = run_website_test(site("endless") + "/", CFG, db=db)  # previously: RunAborted("Website not reachable: Too many redirects")
    run, tests = db.get_run(rid), db.get_test_cases(rid)
    assert run["status"] == "COMPLETED" and run["engine"].startswith("static")
    assert run["failed"] == 0 and run["passed"] == 0 and run["skipped"] == run["total"] > 0  # nothing invented
    by = {t["name"]: t for t in tests}
    assert by["Website loads"]["status"] == "SKIPPED" and "browser" in by["Website loads"]["actual"].lower()
    assert "redirects could not be followed" in run["ai_summary"] and "No browser engine" in run["ai_summary"]
    assert db.list_bugs(rid) == []


class FakeDriver:
    """Stand-in for Selenium (no Chrome in the test sandbox): simulates a browser that follows the redirects."""
    title = "Landing page after redirects"
    page_source = LANDING

    def __init__(self, final): self.current_url, self._final = "", final
    def get(self, url): self.opened = url; self.current_url = self._final
    def execute_script(self, js): return "complete" if "readyState" in js else 1000
    def save_screenshot(self, path): return True
    def set_window_size(self, w, h): pass


class FakeBrowser:
    opened = []

    info = {"browser": "fake"}
    settle = poll = 0

    def __init__(self, *a, **k): self.driver = None
    def start(self): self.driver = FakeDriver(FakeBrowser.final); return self
    def open(self, url, wait=8): FakeBrowser.opened.append(url); self.driver.get(url); return True
    def close(self): pass


def test_unresolved_redirects_use_the_browser_when_available():
    base = site("endless")
    FakeBrowser.final, FakeBrowser.opened = base + "/landing", []
    orig = ex.BrowserSession
    ex.BrowserSession = FakeBrowser
    db = temp_db()
    try:
        rid = run_website_test(base + "/", {**CFG, "engine": "auto", "options": {"links": False, "forms": False, "responsive": False, "console": False}}, db=db)
    finally:
        ex.BrowserSession = orig
    run, by = db.get_run(rid), {t["name"]: t for t in db.get_test_cases(rid)}
    assert FakeBrowser.opened == [base + "/"]  # browser started from the ORIGINAL url and followed the redirects itself
    assert run["engine"] == "Selenium (Chrome)" and run["final_url"] == base + "/landing"
    assert by["Website loads"]["status"] == "PASS" and by["Page title present"]["status"] == "PASS"
    assert by["HTTP availability"]["status"] == "PASS" and "browser loaded" in by["HTTP availability"]["actual"].lower()
    db.delete_run(rid)  # removes the screenshots dir the run created


def test_one_unresolvable_site_never_blocks_the_next():
    res = run_websites([site("endless") + "/", site("err500") + "/", site("long_chain") + "/h1"], CFG, temp_db())
    assert res[0]["run_id"] and not res[0]["error"]
    assert res[1]["run_id"] is None and "500" in res[1]["error"]
    assert res[2]["run_id"] and not res[2]["error"]


# ---- security is unchanged
def test_ssrf_still_enforced_through_redirect_chains():
    av = check_availability(site("bounce", target=site("to_internal") + "/") + "/", **LOOPBACK)  # public hop -> hop -> 169.254.169.254
    assert not av["ok"] and not av["redirect_unresolved"] and "private" in av["error"].lower()
    off = check_availability(site("endless") + "/")  # loopback not enabled
    assert not off["ok"] and not off["redirect_unresolved"] and "loopback" in off["error"].lower()


def test_genuine_failures_are_still_unavailable():
    for kind, word in (("err500", "500"), ("notfound", "404"), ("forbidden", "403")):
        av = check_availability(site(kind) + "/", **LOOPBACK)
        assert not av["ok"] and not av["redirect_unresolved"] and word in av["error"], kind
    assert not check_availability("http://127.0.0.1:9/", **LOOPBACK)["ok"]  # connection refused
    assert "Timed out" in check_availability(site("slow") + "/", timeout=1, **LOOPBACK)["error"]


def test_loop_error_is_a_redirect_error_with_diagnostics_not_a_security_error():
    try:
        safe_request("GET", site("loop") + "/a", **LOOPBACK)
        raise AssertionError("expected RedirectError")
    except RedirectError as e:
        assert e.loop and e.chain and e.last_status == 302 and e.last_url.startswith("http://127.0.0.1")


# ---- what the dashboard shows
def test_dashboard_preview_shows_final_url_and_status():
    old = {k: sys.modules.get(k) for k in ("streamlit", "views", "views.new_run", "views.common")}
    shown = {"md": [], "warn": [], "err": []}
    fake = types.ModuleType("streamlit")
    fake.cache_resource = lambda f=None, **k: (f if f else (lambda g: g))
    fake.markdown = lambda body, *a, **k: shown["md"].append(body)
    fake.warning = lambda m, *a, **k: shown["warn"].append(m)
    fake.error = lambda m, *a, **k: shown["err"].append(m)
    fake.session_state = {}
    fake.__getattr__ = lambda n: (lambda *a, **k: None)
    sys.modules["streamlit"] = fake
    for k in ("views", "views.new_run", "views.common"):
        sys.modules.pop(k, None)
    try:
        from views.new_run import target_preview
        s = {"allow_loopback": True, "allow_private": False, "request_timeout": 2}
        assert target_preview(site("long_chain") + "/h1", s) is True
        assert "final URL: http://127.0.0.1" in shown["md"][-1] and "HTTP 200" in shown["md"][-1] and "Unavailable" not in shown["md"][-1]
        assert target_preview(site("endless") + "/", s) is True  # previously False + "Unavailable: Too many redirects"
        card = shown["md"][-1]
        assert "Reachable" in card and "HTTP 303" in card and "not treated as unavailable" in card and "Unavailable:" not in card
        assert any("redirects could not be followed" in w for w in shown["warn"]) and not shown["err"]
        assert target_preview(site("err500") + "/", s) is False and "Unavailable: HTTP 500" in shown["md"][-1]  # real failures still shown
    finally:
        for k, v in old.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
