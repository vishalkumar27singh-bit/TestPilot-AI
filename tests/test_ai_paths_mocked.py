"""AI integration logic exercised with a MOCKED model (no network, no key needed).
These prove the validation/fallback plumbing, not the quality of any real model's answers."""
import os

from core import ai_engine
from core.failure_analyzer import analyze_failures
from core.models import SiteProfile, TestCase
from core.test_generator import generate_tests
from core.website_analyzer import parse_html
from tests.test_analyzer_and_generator import HTML


class patched:
    def __init__(self, **fns):
        self.fns, self.old = fns, {}

    def __enter__(self):
        os.environ["ANTHROPIC_API_KEY"] = "dummy-test-key"
        for k, f in self.fns.items():
            self.old[k] = getattr(ai_engine, k); setattr(ai_engine, k, f)

    def __exit__(self, *a):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        for k, f in self.old.items():
            setattr(ai_engine, k, f)


def profile():
    return parse_html(HTML, "https://example.com/", SiteProfile(url="https://example.com/"))


def test_ai_tests_merged_but_unsafe_ones_dropped():
    reply = {"tests": [{"name": "Check contact form", "action": "forms", "params": {"index": 1}, "severity": "HIGH"},
                       {"name": "Evil", "action": "run_python", "params": {"code": "os.system('x')"}}]}
    with patched(complete_json=lambda *a, **k: reply):
        tests = generate_tests(profile())
    ai = [t for t in tests if t.source == "ai"]
    assert [t.name for t in ai] == ["Check contact form"]


def test_ai_failure_falls_back_to_rules():
    def boom(*a, **k):
        raise ai_engine.AIUnavailable("simulated outage")
    with patched(complete_json=boom):
        tests = generate_tests(profile())
    assert tests and all(t.source == "rule" for t in tests)


def test_ai_explanations_applied_and_fallback_text_otherwise():
    t = TestCase("UI-1", "Broken links", "Links", "o", ["s"], "e", "broken_links", status="FAIL", error="1 link broken HTTP 404", actual="HTTP 404")
    with patched(complete_json=lambda *a, **k: {"items": [{"id": "UI-1", "explanation": "A link points nowhere.", "probable_cause": "Deleted page", "suggested_fix": "Redirect it"}]}):
        analyze_failures([t], use_ai=True)
    assert t.ai_explanation == "A link points nowhere." and t.suggested_fix == "Redirect it"
    t2 = TestCase("UI-2", "x", "c", "o", ["s"], "e", "page_load", status="FAIL", error="Timed out after 10s")
    analyze_failures([t2], use_ai=False)
    assert "slow" in t2.probable_cause.lower() or "too long" in t2.probable_cause.lower()
    assert "unavailable" in t2.ai_explanation.lower()


def test_api_key_never_leaks_into_errors():
    import requests
    def fake_post(*a, **k):
        raise requests.ConnectionError("boom " + k["headers"]["x-api-key"])
    orig = requests.post
    requests.post = fake_post
    try:
        os.environ["ANTHROPIC_API_KEY"] = "sk-secret-123"
        try:
            ai_engine.complete("s", "p")
        except ai_engine.AIUnavailable as e:
            assert "sk-secret-123" not in str(e) and e.__cause__ is None
    finally:
        requests.post = orig
        os.environ.pop("ANTHROPIC_API_KEY", None)
