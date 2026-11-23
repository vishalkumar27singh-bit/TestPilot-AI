"""Orchestrates the full 15-step workflow for website runs and API runs."""
from __future__ import annotations

import json
import time
from typing import Callable

from config.settings import load_user_settings
from core import ai_engine
from core.failure_analyzer import analyze_failures, build_bug
from core.models import SiteProfile, TestCase, TestFailure, TestSkipped
from core.safety import UnsafeTargetError, check_availability, validate_url
from core.test_generator import generate_tests
from core.website_analyzer import analyze_website
from database.db_manager import DB
from testing.api_tests import run_api_suite
from testing.context import BrowserSession, BrowserUnavailable, RunContext
from testing.registry import ACTIONS, engine_label
from utils.helpers import fmt_duration
from utils.logger import RunLog
from utils.screenshot_manager import ScreenshotManager

ProgressCb = Callable[[float, str], None]


class RunAborted(Exception):
    """Run could not start (invalid/unsafe/unreachable target)."""


def execute_one(ctx: RunContext, tc: TestCase) -> None:
    """Run one handler, record status/actual/duration. Raises nothing for normal failures when called via run_case()."""
    ctx.current_test_id = tc.test_id
    handler = ACTIONS[tc.action]
    t0 = time.perf_counter()
    try:
        tc.actual = handler(ctx, tc.params)
        tc.status = "PASS"
    except TestSkipped as e:
        tc.status, tc.actual = "SKIPPED", str(e)
        raise
    except TestFailure as e:
        tc.status, tc.actual, tc.error, tc.failed_step = "FAIL", str(e), str(e), e.step
        raise
    except UnsafeTargetError as e:
        tc.status, tc.actual, tc.error, tc.failed_step = "FAIL", f"Blocked by safety rules: {e}", str(e), "Safety check"
        raise TestFailure(tc.actual, "Safety check")
    except Exception as e:  # any handler bug / browser crash must not stop the run
        msg = f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"
        tc.status, tc.actual, tc.error, tc.failed_step = "FAIL", f"Unexpected error - {msg}", msg, "Execute test"
        raise TestFailure(tc.actual, "Execute test")
    finally:
        tc.engine = engine_label(tc.action, tc.status, bool(ctx.driver))
        tc.duration = time.perf_counter() - t0
        if tc.status == "FAIL" and ctx.driver:
            failed_shot = ctx.shot("FAILED")
            # evidence captured by the handler at the failing state (e.g. the mobile viewport) beats a screenshot taken afterwards
            own = [p for lab, p in ctx.shots.get(tc.test_id, []) if lab != "FAILED"]
            tc.screenshot = own[-1] if own else (failed_shot or tc.screenshot)
        elif ctx.shots.get(tc.test_id):
            tc.screenshot = ctx.shots[tc.test_id][-1][1]


def run_website_test(url: str, config: dict | None = None, on_log: Callable | None = None, on_progress: ProgressCb | None = None,
                     db: DB | None = None) -> int:
    """Full workflow. Returns the run id. Raises RunAborted if the target is invalid/unreachable."""
    cfg = {**load_user_settings(), "engine": "auto", "via_pytest": False, "options": {}, **(config or {})}
    db = db or DB()
    log = RunLog(on_log)
    prog = on_progress or (lambda f, m: None)
    started = time.perf_counter()
    ai_on = bool(cfg.get("use_ai")) and ai_engine.is_enabled()
    allow = dict(allow_loopback=cfg["allow_loopback"], allow_private=cfg["allow_private"])
    browser: BrowserSession | None = None
    run_id = 0
    try:
        # 1-2 validate + availability
        prog(0.02, "Validating URL")
        log.add("Step 1/12: validating and resolving the URL safely", "step")
        try:
            target = validate_url(url, **allow)
        except UnsafeTargetError as e:
            log.add(f"URL rejected: {e}", "fail"); raise RunAborted(str(e))
        log.add(f"Target {target.url} resolved to {', '.join(target.ips)}", "ok")
        prog(0.06, "Checking availability")
        log.add("Step 2/12: checking website availability", "step")
        av = check_availability(target.url, timeout=cfg["request_timeout"], **allow)
        if not av["ok"]:
            log.add(f"Website unavailable: {av['error']}", "fail"); raise RunAborted(f"Website not reachable: {av['error']}")
        if av["redirect_unresolved"]:
            log.add(f"Website is reachable (last response HTTP {av['status']} at {av['final_url']}) but its redirects could not be fully "
                    f"resolved over HTTP. Not treating this as unavailable.", "warn")
        else:
            log.add(f"Website responded HTTP {av['status']} in {av['time']:.2f}s (final URL: {av['final_url']})", "ok")
        if av["redirects"]:
            log.add("Redirect chain: " + " -> ".join(u for _, u in av["redirects"]) + f" -> {av['final_url']}", "info")
        for n in av["notes"]:
            log.add(n, "warn")

        run_id = db.create_run(target.url, "ui", ai_mode="AI + rules" if ai_on else "Rule-based", config={k: v for k, v in cfg.items()})
        screens = ScreenshotManager(run_id)

        # 3 open browser
        engine = "static HTTP"
        want_browser = cfg["engine"] in ("auto", "selenium")
        browser_reason = "" if want_browser else "The static HTTP engine was selected for this run."
        if want_browser:
            prog(0.10, "Starting browser")
            log.add("Step 3/12: starting Selenium Chrome", "step")
            try:
                browser = BrowserSession(cfg["headless"], cfg["page_load_timeout"]).start()
                # when redirects could not be resolved over HTTP, let the browser follow them from the original URL
                settled = browser.open(target.url if av["redirect_unresolved"] else av["final_url"], cfg.get("render_wait", 8))
                engine = "Selenium (Chrome)"
                i = browser.info
                log.add(f"Real browser running: {i.get('browser', 'chrome')} {i.get('version', '')} (chromedriver {i.get('driver_version', '?')}) on "
                        f"{i.get('platform')}/{i.get('arch')}, {'headless' if i.get('headless') else 'visible window'}", "ok")
                log.add("Browser started and page opened" + ("" if settled else " (page was still changing after the render wait; analyzing what is visible)"), "ok")
            except BrowserUnavailable as e:
                browser_reason = str(e)
                log.add(f"Browser unavailable: {e}", "warn")
                log.add("Falling back to the static HTTP engine: screenshots, console-error and real layout checks will be SKIPPED, "
                        "all other checks still run.", "warn")
                browser = None
            except Exception as e:
                log.add(f"Browser could not open the page ({type(e).__name__}). Falling back to the static HTTP engine: screenshots, "
                        "console-error and real layout checks will be SKIPPED.", "warn")
                if browser:
                    browser.close()
                browser_reason = f"Chrome could not open the page ({type(e).__name__})."
                browser = None
        else:
            log.add("Step 3/12: browser skipped (static HTTP engine selected)", "step")
        db_engine = engine + (" via pytest" if cfg["via_pytest"] else "")

        # 4-5 analyze
        prog(0.18, "Analyzing website")
        log.add("Step 4/12: analyzing website structure", "step")
        if browser and browser.driver:
            final = browser.driver.current_url
            validate_url(final, **allow)  # browser may have been redirected somewhere unsafe
            profile = analyze_website(target.url, html=browser.driver.page_source, final_url=final, **allow)
            profile.status_code, profile.load_time = av["status"], av["time"]
            if av["redirect_unresolved"]:
                profile.redirect_unresolved, profile.status_code = True, 0  # Selenium does not expose HTTP status
                profile.notes.append("The browser followed redirects that a plain HTTP client could not; the HTTP status of the final page "
                                     "is not observable through Selenium.")
        elif av["redirect_unresolved"]:
            # reachable, but there is no page body to analyze without a browser: run only the checks that remain truthful
            profile = SiteProfile(url=target.url, final_url=av["final_url"], status_code=av["status"], load_time=av["time"], redirect_unresolved=True,
                                  notes=["No browser engine is available, so the page content could not be analyzed; element-based tests were not "
                                         "generated and the remaining checks are reported as SKIPPED (not failed)."])
        else:
            profile = analyze_website(target.url, timeout=cfg["request_timeout"], **allow)
        for n in profile.notes:
            log.add(n, "warn")
        log.add(f"Step 5/12: found {len(profile.links)} links, {len(profile.buttons)} buttons, {len(profile.inputs)} inputs, "
                f"{len(profile.forms)} forms, {len(profile.images)} images", "ok")

        # 6 generate
        prog(0.25, "Generating test cases")
        log.add("Step 6/12: generating test cases", "step")
        tests = generate_tests(profile, cfg["options"], ai_on, log)
        log.add(f"{len(tests)} test cases ready", "ok")

        ctx = RunContext(profile=profile, settings=cfg, log=log, browser=browser, screens=screens, browser_reason=browser_reason)
        by_id = {t.test_id: t for t in tests}

        def announce(i, t):
            prog(0.30 + 0.55 * i / max(len(tests), 1), f"Running {t.test_id}: {t.name}")
            log.add(f"[{i + 1}/{len(tests)}] Running {t.test_id} - {t.name}", "step")

        def report(t):
            lvl = {"PASS": "ok", "FAIL": "fail", "SKIPPED": "warn"}.get(t.status, "info")
            log.add(f"{t.test_id} {t.status} ({fmt_duration(t.duration)}) - {t.actual[:140]}", lvl)

        # 7-8 execute (+ screenshots happen inside handlers / on failure)
        log.add("Step 7/12: executing tests" + (" through pytest" if cfg["via_pytest"] else ""), "step")
        counter = {"i": 0}

        def run_case(case_id):
            t = by_id[case_id]
            announce(counter["i"], t); counter["i"] += 1
            try:
                execute_one(ctx, t)
            finally:
                if t.status != "PENDING":
                    report(t)

        if cfg["via_pytest"]:
            from testing.pytest_bridge import run_via_pytest
            run_via_pytest([t.test_id for t in tests], run_case, lambda *_: None)
        else:
            for t in tests:
                try:
                    run_case(t.test_id)
                except (TestFailure, TestSkipped):
                    pass
        for t in tests:  # safety net: anything pytest/handlers never ran
            if t.status == "PENDING":
                t.status, t.actual = "SKIPPED", "Not executed."

        # 9-10 failures + bugs
        prog(0.88, "Analyzing failures")
        log.add("Step 9/12: analyzing failures", "step")
        analyze_failures(tests, ai_on, log)
        bugs = [build_bug(run_id, n + 1, t, profile.final_url) for n, t in enumerate(x for x in tests if x.status == "FAIL")]
        log.add(f"Step 10/12: {len(bugs)} bug report(s) created", "ok" if not bugs else "warn")

        # AI summary
        ai_summary = ""
        if ai_on:
            try:
                fails = [f"{t.name}: {t.actual[:150]}" for t in tests if t.status == "FAIL"][:12]
                ai_summary = ai_engine.complete(
                    "You are a QA lead writing a short executive summary and 3-5 prioritised recommendations.",
                    f"{ai_engine.UNTRUSTED_NOTE}\nSite: {profile.title} ({profile.final_url})\nTotal {len(tests)}, passed {sum(t.status=='PASS' for t in tests)}, "
                    f"failed {len(fails)}.\nFailures: {json.dumps(fails)}", 700)
            except ai_engine.AIUnavailable as e:
                log.add(f"AI summary unavailable ({e}).", "warn")
        if not ai_summary:
            ai_summary = _fallback_summary(tests)
        notes = av["notes"] + profile.notes
        if notes:
            ai_summary = "Notes:\n" + "\n".join(f"- {n}" for n in notes) + "\n\n" + ai_summary

        # 11 save
        prog(0.95, "Saving results")
        log.add("Step 11/12: saving results to SQLite", "step")
        db.add_test_cases(run_id, tests)
        db.add_bugs(bugs)
        db.add_screenshots(run_id, [(tid, lab, p) for tid, lst in ctx.shots.items() for lab, p in lst])
        dur = time.perf_counter() - started
        log.add(f"Step 12/12: finished in {fmt_duration(dur)}: "
                f"{sum(t.status=='PASS' for t in tests)} passed, {len(bugs)} failed, {sum(t.status=='SKIPPED' for t in tests)} skipped", "ok")
        db.finish_run(run_id, "COMPLETED", dur, tests, final_url=profile.final_url, engine=db_engine,
                      profile_json=json.dumps(profile.to_dict(), default=str), log_text=log.text(), ai_summary=ai_summary)
        prog(1.0, "Done")
        return run_id
    except RunAborted:
        if run_id:
            db.finish_run(run_id, "ERROR", time.perf_counter() - started, error="Aborted", log_text=log.text())
        raise
    except ValueError as e:  # e.g. the URL is not an HTML page (JSON, PDF, image...)
        log.add(str(e), "fail")
        if run_id:
            db.finish_run(run_id, "ERROR", time.perf_counter() - started, error=str(e), log_text=log.text())
        raise RunAborted(str(e))
    except Exception as e:
        log.add(f"Run failed unexpectedly: {type(e).__name__}: {e}", "fail")
        if run_id:
            db.finish_run(run_id, "ERROR", time.perf_counter() - started, error=f"{type(e).__name__}: {e}", log_text=log.text())
        raise RunAborted(f"Run failed: {type(e).__name__}: {e}")
    finally:
        if browser:
            browser.close()


def _fallback_summary(tests: list[TestCase]) -> str:
    p, f, s = (sum(t.status == k for t in tests) for k in ("PASS", "FAIL", "SKIPPED"))
    lines = [f"{p} passed, {f} failed, {s} skipped out of {len(tests)} tests (rule-based summary; no AI key configured)."]
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    for t in sorted((x for x in tests if x.status == "FAIL"), key=lambda x: order.get(x.severity, 9))[:5]:
        lines.append(f"- [{t.severity}] {t.name}: {t.suggested_fix}")
    if not f:
        lines.append("No failures detected in the executed checks.")
    return "\n".join(lines)


def run_api_test(url: str, method="GET", headers=None, body=None, expected_status=None, max_time_ms=2000, expected_keys=None,
                 confirm_destructive=False, config: dict | None = None, db: DB | None = None) -> int:
    """Run the API suite, analyse failures, store everything. Raises ApiConfigError for invalid input."""
    cfg = {**load_user_settings(), **(config or {})}
    db = db or DB()
    ai_on = bool(cfg.get("use_ai")) and ai_engine.is_enabled()
    started = time.perf_counter()
    cases, summary = run_api_suite(url, method, headers, body, expected_status, max_time_ms, expected_keys, confirm_destructive, settings=cfg)
    run_id = db.create_run(url, "api", engine="requests", ai_mode="AI + rules" if ai_on else "Rule-based",
                           config={"method": method, "expected_status": expected_status, "max_time_ms": max_time_ms, "expected_keys": expected_keys})
    analyze_failures(cases, ai_on)
    bugs = [build_bug(run_id, n + 1, t, url) for n, t in enumerate(x for x in cases if x.status == "FAIL")]
    db.add_test_cases(run_id, cases)
    db.add_bugs(bugs)
    safe_summary = {k: v for k, v in summary.items() if k != "request_body"}
    db.finish_run(run_id, "COMPLETED", time.perf_counter() - started, cases, final_url=url, profile_json=json.dumps(safe_summary, default=str),
                  ai_summary=_fallback_summary(cases))
    return run_id


def run_websites(urls: list[str], config: dict | None = None, db: DB | None = None, on_log: Callable | None = None) -> list[dict]:
    """Test several different websites one after another. A failure on one site (unsafe, down, redirect loop, crash)
    is recorded for that site only and never stops the others. Returns [{url, run_id, error}]."""
    db = db or DB()
    results = []
    for url in urls:
        try:
            results.append({"url": url, "run_id": run_website_test(url, config, on_log, None, db), "error": ""})
        except RunAborted as e:
            results.append({"url": url, "run_id": None, "error": str(e)})
        except Exception as e:  # last-resort isolation
            results.append({"url": url, "run_id": None, "error": f"{type(e).__name__}: {e}"})
    return results
