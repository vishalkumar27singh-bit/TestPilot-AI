"""Generate test cases from a SiteProfile.

1. Rule-based generator (always available).
2. Optional AI augmentation. The model only proposes *declarative* tests that reference whitelisted
   actions; `validate_ai_tests` rejects anything else, so no AI-written code is ever executed.
"""
from __future__ import annotations

import json

from core import ai_engine
from core.models import SEVERITIES, SiteProfile, TestCase
from testing.registry import ACTION_PARAMS, ACTIONS, PARAM_RULES


def rule_based_tests(profile: SiteProfile, options: dict | None = None) -> list[TestCase]:
    o = {"links": True, "forms": True, "responsive": True, "console": True, "click": True, **(options or {})}
    T: list[TestCase] = []

    def add(name, cat, obj, steps, exp, action, sev, **params):
        T.append(TestCase(test_id=f"UI-{len(T) + 1:03d}", name=name, category=cat, objective=obj, steps=steps,
                          expected=exp, action=action, params=params, severity=sev))

    add("Website loads", "Availability", "Confirm the page opens and finishes loading.", ["Open the URL in the browser", "Wait for document ready"],
        "Page loads fully without errors", "page_load", "CRITICAL")
    add("HTTP availability", "Availability", "Confirm the server answers with a success status.", ["Request the URL", "Read status code and timing"],
        "HTTP 2xx/3xx in a reasonable time", "http_availability", "CRITICAL")
    add("Page title present", "Content", "Confirm the page has a meaningful <title>.", ["Read document title"], "Non-empty title", "page_title", "MEDIUM")
    if profile.nav_links or any(l["internal"] for l in profile.links):
        add("Navigation links work", "Navigation", "Confirm primary navigation links are visible and lead to working pages.",
            ["Locate navigation links", "Open up to 3 internal links", "Return to start page"], "Links load non-empty pages", "navigation_links", "HIGH")
    if profile.links and o["links"]:
        add("No broken links", "Links", "Detect links that return client/server errors.", ["Collect unique links", "Request each link (HEAD, then GET)"],
            "No link returns 4xx/5xx or times out", "broken_links", "HIGH")
    if profile.buttons:
        add("Buttons are usable", "Interaction", "Confirm buttons are visible, enabled and have accessible names.",
            ["Locate each button", "Check visibility, state and label"], "All visible buttons are enabled and labelled", "buttons", "MEDIUM")
    if profile.buttons and o["click"]:
        add("Safe buttons can be clicked", "Interaction", "Click standalone, non-destructive buttons and confirm the page keeps working.",
            ["Pick buttons outside forms and not named delete/pay/send/submit", "Click each one", "Check console errors and URL safety"],
            "Clicks cause no console errors or unsafe navigation", "click_buttons", "MEDIUM")
    if profile.inputs:
        add("Input fields accept text", "Interaction", "Confirm text fields can be typed into.", ["Locate each input", "Type sample data", "Verify value", "Clear field"],
            "Each field keeps the typed value", "input_fields", "MEDIUM")
    if o["forms"]:
        for f in profile.forms[:3]:
            add(f"Form #{f['index'] + 1} structure ({f['id'] or f['method'].upper()})", "Forms", "Confirm the form is complete and submittable.",
                ["Locate the form", "Check fields, submit control and method"], "Form has fields, a submit button and a valid method", "forms", "HIGH", index=f["index"])
    if profile.search_forms:
        add("Search box works", "Search", "Confirm the search box accepts a query and returns results.", ["Type 'test' into the search box", "Submit", "Check navigation"],
            "Search runs and shows a results page", "search_box", "HIGH")
    if profile.images:
        add("Images render", "Media", "Confirm images load correctly.", ["Check each image loads"], "No broken images", "images_loaded", "MEDIUM")
        add("Images have alt text", "Accessibility", "Confirm images have alt attributes.", ["Inspect <img> tags"], "Every image has alt text", "image_alt_text", "LOW")
    if o["responsive"]:
        add("Viewport meta tag present (static HTML check)", "Responsive", "Check the markup declares a mobile viewport.", ["Inspect <head>"],
            "A viewport meta tag exists", "viewport_meta", "MEDIUM")
        for vp, w, h in (("desktop", 1366, 768), ("tablet", 768, 1024), ("mobile", 390, 844)):
            add(f"Responsive layout: {vp} ({w}x{h})", "Responsive", f"Confirm the page fits a {vp} viewport without horizontal scrolling.",
                [f"Resize the real browser viewport to {w}x{h}", "Measure document width", "Capture screenshot"], "No horizontal overflow", "responsive", "MEDIUM" if vp != "mobile" else "HIGH", viewport=vp)
    if o["console"]:
        add("No browser console errors", "Quality", "Detect JavaScript/network errors logged by the browser.", ["Reload page", "Read console log"], "No SEVERE console entries", "console_errors", "MEDIUM")
    return T


def validate_ai_tests(raw, profile: SiteProfile, start: int, cap: int = 8) -> tuple[list[TestCase], list[str]]:
    """Turn raw AI JSON into safe TestCase objects. Returns (accepted, rejection reasons)."""
    rejected: list[str] = []
    items = raw.get("tests", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return [], ["AI output was not a list of tests"]
    out: list[TestCase] = []
    limits = {"buttons": len(profile.buttons), "input_fields": len(profile.inputs), "forms": len(profile.forms), "check_link": len(profile.links)}
    for it in items[:cap * 2]:
        try:
            action = it["action"]
            if action not in ACTIONS:
                rejected.append(f"unknown action '{str(action)[:30]}'"); continue
            params = {}
            for k, v in (it.get("params") or {}).items():
                if k not in ACTION_PARAMS.get(action, set()) or not PARAM_RULES[k](v):
                    rejected.append(f"invalid param {k} for {action}"); params = None; break
                params[k] = v
            if params is None:
                continue
            if "index" in params and params["index"] >= limits.get(action, 0):
                rejected.append(f"index out of range for {action}"); continue
            sev = str(it.get("severity", "MEDIUM")).upper()
            steps = [str(s)[:160] for s in (it.get("steps") or [])][:8] or ["Run the check"]
            out.append(TestCase(test_id=f"AI-{start + len(out) + 1:03d}", name=str(it["name"])[:100], category=str(it.get("category", "AI-suggested"))[:30],
                                objective=str(it.get("objective", ""))[:250], steps=steps, expected=str(it.get("expected", ""))[:250],
                                action=action, params=params, severity=sev if sev in SEVERITIES else "MEDIUM", source="ai"))
        except (KeyError, TypeError, AttributeError):
            rejected.append("malformed test entry")
        if len(out) >= cap:
            break
    return out, rejected


def ai_tests(profile: SiteProfile, existing: list[TestCase]) -> tuple[list[TestCase], list[str]]:
    summary = {"title": profile.title, "features": profile.features,
               "buttons": [{"i": i, "text": b["text"]} for i, b in enumerate(profile.buttons[:15])],
               "inputs": [{"i": i, "type": x["type"], "name": x["name"]} for i, x in enumerate(profile.inputs[:15])],
               "forms": [{"i": f["index"], "method": f["method"], "fields": f["field_names"], "search": f["is_search"]} for f in profile.forms[:8]],
               "links": [{"i": i, "text": l["text"], "internal": l["internal"]} for i, l in enumerate(profile.links[:20])]}
    prompt = (f"{ai_engine.UNTRUSTED_NOTE}\n\nPage profile:\n{json.dumps(summary)[:6000]}\n\nAlready planned: {[t.name for t in existing]}\n\n"
              "Propose up to 6 ADDITIONAL, non-duplicate tests that fit this site's specific functionality. Each test is JSON: "
              '{"name","objective","steps":[...],"expected","category","severity":"LOW|MEDIUM|HIGH|CRITICAL","action","params"}. '
              f"`action` MUST be one of {sorted(a for a in ACTIONS if a in ACTION_PARAMS)}. "
              "params: buttons/input_fields/forms/check_link take {\"index\": <i from the profile>}; responsive takes {\"viewport\": \"desktop|tablet|mobile\"}; "
              "broken_links takes {\"max_links\": int<=50}. Return {\"tests\": [...]}.")
    raw = ai_engine.complete_json("You are a senior QA engineer designing safe, declarative web tests.", prompt, 2500)
    return validate_ai_tests(raw, profile, len(existing))


def generate_tests(profile: SiteProfile, options: dict | None = None, use_ai: bool = True, log=None) -> list[TestCase]:
    tests = rule_based_tests(profile, options)
    if use_ai and ai_engine.is_enabled():
        try:
            extra, rejected = ai_tests(profile, tests)
            tests += extra
            if log:
                log.add(f"AI proposed {len(extra)} extra validated test(s); rejected {len(rejected)} unsafe/invalid.", "ok")
        except ai_engine.AIUnavailable as e:
            if log:
                log.add(f"AI test generation unavailable ({e}); using rule-based tests only.", "warn")
    elif log:
        log.add("AI key not configured: using the rule-based test generator.", "info")
    return tests
