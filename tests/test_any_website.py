"""General-purpose behaviour: many different (local, simulated) websites, generic redirect handling, no hard-coded targets."""
import re
import socket
from pathlib import Path

from core.safety import RedirectError, UnsafeTargetError, check_availability, safe_request, validate_url
from core.test_executor import run_websites
from testing.context import wait_for_render
from tests.helpers import LOOPBACK, ROOT, base_url, temp_db
from tests.multisite import site
from utils.helpers import urls_equivalent

CFG = {"engine": "static", "allow_loopback": True, "use_ai": False, "request_timeout": 1}


def _farm():
    static = base_url() + "/index.html"
    return [  # failing/odd sites first, healthy ones last: later sites must still run
        ("to_internal", site("to_internal") + "/"), ("loop", site("loop") + "/a"), ("err500", site("err500") + "/"),
        ("notfound", site("notfound") + "/"), ("forbidden", site("forbidden") + "/"), ("ratelimited", site("ratelimited") + "/"),
        ("slow", site("slow") + "/"), ("json", site("json") + "/"),
        ("static", static), ("shell", site("shell") + "/"), ("cookie", site("cookie") + "/"), ("login", site("login") + "/"),
        ("sleeping", site("sleeping") + "/"), ("minimal", site("minimal") + "/"), ("broken_html", site("broken_html") + "/"),
        ("head405", site("head405") + "/"), ("bounce", site("bounce", target=static) + "/"), ("empty", site("empty") + "/"),
    ]


_cache = {}


def matrix():
    if "m" not in _cache:
        db = temp_db()
        farm = _farm()
        res = run_websites([u for _, u in farm], CFG, db)
        _cache["m"] = (db, {name: (r, farm[i][1]) for i, (name, _) in enumerate(farm) for r in [res[i]]})
    return _cache["m"]


def _tests(db, name):
    return {t["name"]: t for t in db.get_test_cases(matrix()[1][name][0]["run_id"])}


def test_every_site_gets_its_own_result_and_failures_do_not_stop_others():
    db, m = matrix()
    assert len(m) == 18 and all("run_id" in r and "error" in r for r, _ in m.values())
    good = [n for n, (r, _) in m.items() if r["run_id"]]
    assert set(good) >= {"static", "shell", "cookie", "login", "sleeping", "minimal", "broken_html", "head405", "bounce", "empty", "loop"}
    assert all(db.get_run(m[n][0]["run_id"])["status"] == "COMPLETED" for n in good if n != "json")


def test_each_failure_mode_gives_a_specific_clear_message():
    _, m = matrix()
    expect = {"to_internal": "private", "err500": "500", "notfound": "404", "forbidden": "denied",
              "ratelimited": "429", "slow": "Timed out", "json": "HTML"}
    for name, word in expect.items():
        err = m[name][0]["error"]
        assert m[name][0]["run_id"] is None or name == "json"
        assert word.lower() in err.lower(), (name, err)


def test_redirect_loop_is_reachable_but_unresolved_not_unavailable():
    db, m = matrix()
    r, _ = m["loop"]
    assert r["run_id"] and not r["error"]  # a server that answers but loops is not "down"
    run = db.get_run(r["run_id"])
    assert run["status"] == "COMPLETED" and run["failed"] == 0 and "redirects could not be followed" in run["ai_summary"]


def test_cookie_gated_redirects_work_like_a_browser():
    db, m = matrix()
    assert db.get_run(m["cookie"][0]["run_id"])["final_url"].endswith("/home")


def test_cross_site_redirect_tests_the_final_page():
    db, m = matrix()
    run = db.get_run(m["bounce"][0]["run_id"])
    assert run["final_url"] == m["static"][1] and run["url"] != run["final_url"]
    assert "No broken links" in _tests(db, "bounce")


def test_tests_follow_detected_elements():
    db, _ = matrix()
    static, minimal, shell = set(_tests(db, "static")), set(_tests(db, "minimal")), set(_tests(db, "shell"))
    assert {"Navigation links work", "No broken links", "Buttons are usable", "Search box works", "Images render"} <= static
    assert not ({"Navigation links work", "No broken links", "Buttons are usable", "Search box works", "Images render"} & minimal)
    assert {"Website loads", "Page title present"} <= minimal and len(shell) < len(static)
    assert "Navigation links work" not in shell


def test_js_rendered_apps_are_flagged_not_silently_mis_tested():
    db, m = matrix()
    summary = db.get_run(m["shell"][0]["run_id"])["ai_summary"]
    assert "rendered by JavaScript" in summary and "Selenium" in summary
    assert _tests(db, "shell")["No browser console errors"]["status"] == "SKIPPED"  # not faked


def test_login_wall_sleeping_app_and_html_edge_cases():
    db, m = matrix()
    assert "login" in db.get_run(m["login"][0]["run_id"])["ai_summary"].lower()
    assert "asleep" in db.get_run(m["sleeping"][0]["run_id"])["ai_summary"].lower()
    assert _tests(db, "head405")["No broken links"]["status"] == "PASS"  # HEAD 405 -> GET fallback
    assert _tests(db, "broken_html")["Website loads"]["status"] == "PASS"
    assert _tests(db, "empty")["Page title present"]["status"] == "FAIL"
    assert _tests(db, "static")["No broken links"]["status"] == "FAIL"  # real defects still found


# ---------------- generic redirect handling
def test_cookie_jar_and_loop_detection():
    r = safe_request("GET", site("cookie") + "/", **LOOPBACK)
    assert r.status_code == 200 and [c for c, _ in r.redirect_chain] == [302, 302]
    try:
        safe_request("GET", site("loop") + "/a", **LOOPBACK)
        raise AssertionError("loop not detected")
    except RedirectError as e:
        assert "loop" in str(e).lower() and not isinstance(e, UnsafeTargetError)


def test_credentials_stripped_on_cross_origin_redirect_kept_on_same_origin():
    import json
    b = site("echo")
    a = site("echo", target=b)
    h = {"Authorization": "Bearer secret", "X-Api-Key": "k"}
    cross = json.loads(safe_request("GET", a + "/r", headers=h, **LOOPBACK).text)
    same = json.loads(safe_request("GET", a + "/same", headers=h, **LOOPBACK).text)
    assert "authorization" not in cross and "x-api-key" not in cross
    assert same["authorization"] == "Bearer secret"


def test_availability_reports_redirects_and_auth_notes():
    av = check_availability(site("login") + "/", **LOOPBACK)
    assert av["ok"] and av["final_url"].endswith("/login") and any("login" in n.lower() for n in av["notes"])
    assert "403" in check_availability(site("forbidden") + "/", **LOOPBACK)["error"]


def test_ssrf_protection_still_blocks_everything_by_default():
    for kind in ("static", "shell", "minimal"):
        av = check_availability(site("minimal") + "/")  # loopback not enabled
        assert not av["ok"] and "loopback" in av["error"].lower()
    assert not check_availability(site("to_internal") + "/", **LOOPBACK)["ok"]  # hostile redirect still blocked w/ loopback on


# ---------------- nothing hard-coded
def test_any_hostname_is_accepted_when_it_resolves_publicly_and_rejected_when_private():
    orig = socket.getaddrinfo
    try:
        socket.getaddrinfo = lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34" if "internal" not in host else "10.1.2.3", 0))]
        for u in ("my-app.streamlit.app", "https://shop.example.org/path?q=1", "examseat.example/", "http://sub.domain.co.uk:8080/x"):
            assert validate_url(u).ips == ["93.184.216.34"], u
        try:
            validate_url("https://app.internal-looking.example")
            raise AssertionError("private resolution must be blocked")
        except UnsafeTargetError:
            pass
    finally:
        socket.getaddrinfo = orig


def test_no_site_specific_strings_in_engine_code():
    bad = re.compile(r"examseat|\.streamlit\.app|share\.streamlit\.io", re.I)
    for d in ("core", "testing", "views", "utils", "config", "database", "reports"):
        for p in (ROOT / d).rglob("*.py"):
            assert not bad.search(p.read_text()), p
    assert not bad.search((ROOT / "app.py").read_text())


# ---------------- JS-render waiting & URL comparison
class FakeDriver:
    def __init__(self, sizes, ready="complete", boom=False):
        self.sizes, self.i, self.ready, self.boom = sizes, 0, ready, boom

    def execute_script(self, js):
        if self.boom:
            raise RuntimeError("driver crashed")
        if "readyState" in js:
            return self.ready
        v = self.sizes[min(self.i, len(self.sizes) - 1)]
        self.i += 1
        return v


def _clock():
    t = [0.0]
    return (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))


def test_wait_for_render_settles_times_out_and_never_raises():
    now, sleep = _clock()
    assert wait_for_render(FakeDriver([0, 0, 100, 200, 200, 200, 200, 200, 200]), 8, 1.0, 0.4, sleep, now) is True
    now, sleep = _clock()
    assert wait_for_render(FakeDriver(list(range(1, 500))), 3, 1.0, 0.4, sleep, now) is False  # keeps changing
    now, sleep = _clock()
    assert wait_for_render(FakeDriver([5], ready="loading"), 2, 1.0, 0.4, sleep, now) is False
    assert wait_for_render(FakeDriver([5], boom=True), 2) is False


def test_urls_equivalent_ignores_fragment_case_and_trailing_slash():
    assert urls_equivalent("https://A.com/x/#top", "https://a.com/x")
    assert not urls_equivalent("https://a.com/x", "https://a.com/y") and not urls_equivalent("https://a.com/?p=1", "https://a.com/?p=2")
