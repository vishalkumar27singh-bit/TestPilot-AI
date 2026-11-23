"""API testing engine. One real request drives several assertions; destructive methods need confirmation."""
from __future__ import annotations

import json
import time
import uuid
from urllib.parse import urlparse, urlunparse

import requests

from core.models import TestCase, TestFailure, TestSkipped
from core.safety import UnsafeTargetError, safe_request, validate_url

ALLOWED_METHODS = ("GET", "POST", "PUT", "DELETE")
DESTRUCTIVE = {"POST", "PUT", "DELETE"}


class ApiConfigError(ValueError):
    pass


def _tc(n, name, objective, steps, expected, severity) -> TestCase:
    return TestCase(test_id=f"API-{n:03d}", name=name, category="API", objective=objective, steps=steps,
                    expected=expected, action="api", severity=severity)


def _run(tc: TestCase, fn):
    t0 = time.perf_counter()
    try:
        tc.actual = fn()
        tc.status = "PASS"
    except TestSkipped as e:
        tc.status, tc.actual = "SKIPPED", str(e)
    except TestFailure as e:
        tc.status, tc.actual, tc.error, tc.failed_step = "FAIL", str(e), str(e), e.step
    except Exception as e:  # keep the suite alive
        tc.status, tc.actual, tc.error = "FAIL", f"Unexpected error: {type(e).__name__}: {e}", str(e)
    tc.duration = time.perf_counter() - t0
    return tc


def run_api_suite(url: str, method: str = "GET", headers: dict | None = None, body: str | None = None,
                  expected_status: int | None = None, max_time_ms: int = 2000, expected_keys: list[str] | None = None,
                  confirm_destructive: bool = False, probe_errors: bool = True, settings: dict | None = None,
                  timeout: int = 15) -> tuple[list[TestCase], dict]:
    """Returns (test cases, request/response summary). Raises ApiConfigError for invalid input."""
    settings = settings or {}
    method = method.upper()
    if method not in ALLOWED_METHODS:
        raise ApiConfigError(f"Method must be one of {', '.join(ALLOWED_METHODS)}.")
    if method in DESTRUCTIVE and not confirm_destructive:
        raise ApiConfigError(f"{method} requests can change data on the server. Tick the confirmation box to proceed.")
    allow = dict(allow_loopback=settings.get("allow_loopback", False), allow_private=settings.get("allow_private", False))
    try:
        validate_url(url, **allow)
    except UnsafeTargetError as e:
        raise ApiConfigError(str(e))
    json_body = None
    if body and body.strip() and method in ("POST", "PUT"):
        try:
            json_body = json.loads(body)
        except ValueError as e:
            raise ApiConfigError(f"Request body is not valid JSON: {e}")
    headers = {k: v for k, v in (headers or {}).items() if k.strip()}
    if not any(k.lower() == "accept" for k in headers):
        headers["Accept"] = "application/json, */*;q=0.8"

    resp, err, elapsed = None, "", 0.0
    t0 = time.perf_counter()
    try:
        resp = safe_request(method, url, headers=headers, json_body=json_body, timeout=timeout, **allow)
    except UnsafeTargetError as e:
        err = str(e)
    except requests.exceptions.Timeout:
        err = f"Request timed out after {timeout}s."
    except requests.exceptions.SSLError:
        err = "SSL/TLS certificate error."
    except requests.exceptions.RequestException as e:
        err = f"Connection failed ({type(e).__name__})."
    elapsed = time.perf_counter() - t0

    summary = {"url": url, "method": method, "error": err, "elapsed_ms": round(elapsed * 1000, 1),
               "status": resp.status_code if resp is not None else None,
               "headers": dict(resp.headers) if resp is not None else {},
               "body_preview": (resp.text[:4000] if resp is not None else ""), "request_body": body or ""}

    parsed = {"ok": False, "value": None}
    if resp is not None:
        try:
            parsed = {"ok": True, "value": resp.json()}
        except ValueError:
            pass

    def need_response():
        if resp is None:
            raise TestFailure(err or "No response received.", "Send request")

    def t_status():
        need_response()
        s = resp.status_code
        if expected_status is not None:
            if s != expected_status:
                raise TestFailure(f"Expected HTTP {expected_status} but received HTTP {s}.", "Check status code")
            return f"HTTP {s} as expected."
        if not 200 <= s < 300:
            raise TestFailure(f"Expected a 2xx success status but received HTTP {s}.", "Check status code")
        return f"HTTP {s}."

    def t_time():
        need_response()
        ms = elapsed * 1000
        if ms > max_time_ms:
            raise TestFailure(f"Response took {ms:.0f} ms (limit {max_time_ms} ms).", "Measure response time")
        return f"{ms:.0f} ms (limit {max_time_ms} ms)."

    def t_headers():
        need_response()
        h = {k.lower(): v for k, v in resp.headers.items()}
        if resp.content and "content-type" not in h:
            raise TestFailure("Response has a body but no Content-Type header.", "Inspect headers")
        missing = [x for x in ("date",) if x not in h]
        ct = h.get("content-type", "(none)")
        if parsed["ok"] and "json" not in ct.lower():
            raise TestFailure(f"Body is JSON but Content-Type is '{ct}'.", "Compare Content-Type with body")
        return f"{len(h)} headers; Content-Type: {ct}" + (f"; missing {missing}" if missing else "") + "."

    def t_json():
        need_response()
        if resp.status_code == 204 or not resp.content:
            raise TestSkipped("Empty response body (HTTP %d): nothing to parse." % resp.status_code)
        if not parsed["ok"]:
            raise TestFailure("Response body is not valid JSON. Preview: " + resp.text[:100].replace("\n", " "), "Parse JSON")
        return "Body parsed as valid JSON."

    def t_structure():
        need_response()
        if not parsed["ok"]:
            raise TestSkipped("Response is not JSON.")
        v = parsed["value"]
        if v in ({}, [], None, ""):
            raise TestFailure("JSON body is empty.", "Inspect structure")
        if expected_keys:
            obj = v[0] if isinstance(v, list) and v else v
            if not isinstance(obj, dict):
                raise TestFailure("Expected keys can only be checked on a JSON object or a list of objects.", "Inspect structure")
            miss = [k for k in expected_keys if k not in obj]
            if miss:
                raise TestFailure(f"Missing expected key(s): {', '.join(miss)}. Present: {', '.join(list(obj)[:10])}", "Check expected keys")
            return f"All expected keys present ({', '.join(expected_keys)})."
        kind = f"object with {len(v)} keys" if isinstance(v, dict) else f"array with {len(v)} items" if isinstance(v, list) else type(v).__name__
        return f"Non-empty JSON {kind}."

    def t_errors():
        if not probe_errors:
            raise TestSkipped("Error-response probe disabled.")
        pr = urlparse(url)
        bogus = urlunparse(pr._replace(path=pr.path.rstrip("/") + f"/__testpilot_{uuid.uuid4().hex[:8]}", query=""))
        try:
            r = safe_request("GET", bogus, timeout=timeout, headers=headers, **allow)
        except Exception as e:
            raise TestFailure(f"Probe request failed: {type(e).__name__}", "Request non-existent resource")
        if r.status_code < 400:
            raise TestFailure(f"A non-existent path returned HTTP {r.status_code} instead of 404.", "Request non-existent resource")
        if r.status_code >= 500:
            raise TestFailure(f"A non-existent path caused a server error (HTTP {r.status_code}).", "Request non-existent resource")
        try:
            r.json(); shape = "with a JSON error body"
        except ValueError:
            shape = "with a non-JSON body"
        return f"Non-existent path returned HTTP {r.status_code} {shape}."

    cases = [
        _run(_tc(1, "Response status code", "Verify the endpoint returns the expected status.", [f"Send {method} {url}", "Read status code"],
                 "HTTP status matches expectation", "CRITICAL"), t_status),
        _run(_tc(2, "Response time", "Verify the endpoint responds quickly.", ["Measure elapsed time"], f"Under {max_time_ms} ms", "MEDIUM"), t_time),
        _run(_tc(3, "Response headers", "Verify essential headers are present and consistent.", ["Inspect response headers"],
                 "Content-Type present and consistent with body", "LOW"), t_headers),
        _run(_tc(4, "JSON validity", "Verify the body is parseable JSON.", ["Parse response body as JSON"], "Valid JSON", "HIGH"), t_json),
        _run(_tc(5, "Response structure", "Verify the JSON is non-empty" + (" and has the expected keys." if expected_keys else "."),
                 ["Inspect JSON structure"], "Non-empty JSON with expected keys", "HIGH"), t_structure),
        _run(_tc(6, "Error response handling", "Verify unknown resources return a clean 4xx error (non-destructive GET probe).",
                 ["GET a random non-existent path under the endpoint"], "HTTP 404 (or other 4xx), never 5xx or 200", "MEDIUM"), t_errors),
    ]
    return cases, summary
