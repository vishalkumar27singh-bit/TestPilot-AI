"""Failure explanations (rule-based fallback + optional AI) and bug-report creation."""
from __future__ import annotations

import json
import re

from core import ai_engine
from core.models import TestCase
from utils.helpers import now_iso

RULES = [
    (r"timed? ?out|timeout", "The server or page took too long to respond.", "Check server load and performance; optimise slow resources; raise the timeout only if the delay is expected."),
    (r"ssl|certificate", "The site's TLS certificate is invalid, expired or misconfigured.", "Renew/repair the certificate and make sure the full chain is served."),
    (r"horizontal overflow", "An element is wider than the viewport (fixed widths, unscaled images or tables).", "Use responsive units (%, rem, max-width:100%), wrap wide tables in overflow containers, and test with media queries."),
    (r"viewport", "The page lacks a viewport meta tag, so phones render the desktop layout.", "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> to <head>."),
    (r"alt attribute", "Images are missing alt text, which hurts accessibility and SEO.", "Add descriptive alt text (alt=\"\" for purely decorative images)."),
    (r"image\(s\) failed|images? failed to render", "One or more image files are missing or cannot be loaded.", "Fix the image URL or upload the missing file; check the path's capitalisation and hosting."),
    (r"broken|HTTP 404", "A link or resource points to a page that does not exist.", "Fix or remove the URL, or add a redirect from the old address."),
    (r"HTTP 5\d\d", "The server raised an internal error while handling the request.", "Inspect server logs for the failing endpoint and fix the underlying exception."),
    (r"HTTP 4\d\d", "The request was rejected (missing page, permissions or bad input).", "Verify the URL, authentication and request parameters."),
    (r"console error", "JavaScript or resource-loading errors occurred in the browser.", "Open DevTools > Console, reproduce the error and fix the failing script or missing resource."),
    (r"no accessible name|no label|no text", "An interactive control has no readable name for users or screen readers.", "Add visible text, an <label for=...> or an aria-label."),
    (r"no submit", "A form has no submit control, so users cannot send it.", "Add a <button type=\"submit\"> inside the form."),
    (r"not valid json|json", "The response is not well-formed JSON or has the wrong Content-Type.", "Return valid JSON and set Content-Type: application/json."),
    (r"missing expected key", "The response lacks fields clients depend on.", "Restore the field or update the API contract/documentation."),
    (r"did not accept|could not be typed", "A field rejects or alters typed input (JS handlers, readonly or overlays).", "Check input masks, event handlers and whether another element covers the field."),
    (r"not navigate|did not navigate", "Submitting the search produced no visible change.", "Verify the search form's action/JS handler and that results are rendered."),
]


def fallback_analysis(tc: TestCase) -> tuple[str, str]:
    text = f"{tc.error} {tc.actual}"
    for pat, cause, fix in RULES:
        if re.search(pat, text, re.I):
            return cause, fix
    return ("The behaviour observed did not match what the test expected.",
            "Reproduce the steps manually, compare with the expected result, and inspect the page source / network tab for the cause.")


def analyze_failures(tests: list[TestCase], use_ai: bool = True, log=None) -> None:
    """Fills probable_cause / suggested_fix / ai_explanation on every failed test (in place)."""
    failed = [t for t in tests if t.status == "FAIL"]
    for t in failed:
        t.probable_cause, t.suggested_fix = fallback_analysis(t)
        t.ai_explanation = "AI explanation unavailable (no API key configured). The rule-based analysis above was used."
    if not failed or not use_ai or not ai_engine.is_enabled():
        return
    try:
        payload = [{"id": t.test_id, "name": t.name, "failed_step": t.failed_step, "error": t.error[:400], "expected": t.expected} for t in failed[:12]]
        res = ai_engine.complete_json(
            "You are a QA engineer explaining website test failures to a student. Be concise and plain-spoken.",
            f"{ai_engine.UNTRUSTED_NOTE}\nFailures:\n{json.dumps(payload)}\n\nReturn {{\"items\":[{{\"id\":..,\"explanation\":\"2 plain sentences\","
            "\"probable_cause\":\"..\",\"suggested_fix\":\"..\"}}]}", 2000)
        by_id = {t.test_id: t for t in failed}
        for it in res.get("items", []):
            t = by_id.get(it.get("id"))
            if t:
                t.ai_explanation = str(it.get("explanation", ""))[:500] or t.ai_explanation
                t.probable_cause = str(it.get("probable_cause", ""))[:300] or t.probable_cause
                t.suggested_fix = str(it.get("suggested_fix", ""))[:400] or t.suggested_fix
        if log:
            log.add("AI explained the failed tests.", "ok")
    except (ai_engine.AIUnavailable, AttributeError, TypeError) as e:
        if log:
            log.add(f"AI failure analysis unavailable ({e}); using rule-based explanations.", "warn")


def build_bug(run_id: int, n: int, tc: TestCase, url: str) -> dict:
    return {
        "bug_id": f"BUG-{run_id:04d}-{n:02d}", "run_id": run_id, "test_id": tc.test_id,
        "title": f"[{tc.severity}] {tc.name} failed" + (f" on {url}" if url else ""),
        "description": f"{tc.objective} Result: {tc.actual or tc.error}",
        "severity": tc.severity,
        "steps_to_reproduce": "\n".join(f"{i + 1}. {s}" for i, s in enumerate(tc.steps)),
        "expected": tc.expected, "actual": tc.actual or tc.error, "screenshot": tc.screenshot,
        "created_at": now_iso(), "suggested_solution": tc.suggested_fix, "status": "OPEN",
    }
