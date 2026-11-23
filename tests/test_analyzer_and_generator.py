from core.models import SiteProfile
from core.test_generator import rule_based_tests, validate_ai_tests
from core.website_analyzer import analyze_website, parse_html
from testing.registry import ACTIONS
from tests.helpers import LOOPBACK, base_url

HTML = """<html lang=en><title>T</title><meta name=viewport content=x><nav><a href=/a>A</a></nav><h1>Hi</h1>
<form id=s role=search action=/search method=get><input name=q type=search><button>Go</button></form>
<form action=/p method=post><input name=email type=email><input type=submit value=Send></form>
<button>Ok</button><img src=a.png><img src=b.png alt=b><a href="mailto:x@y.z">m</a>"""


def prof():
    return parse_html(HTML, "https://example.com/", SiteProfile(url="https://example.com/"))


def test_parse_extracts_elements():
    p = prof()
    assert p.title == "T" and p.has_viewport_meta and len(p.forms) == 2 and p.forms[0]["is_search"]
    assert [l["href"] for l in p.links] == ["https://example.com/a"] and p.nav_links
    assert all(not l["href"].startswith("mailto") for l in p.links)
    assert len(p.images) == 2 and p.images[0]["has_alt"] is False


def test_analyze_live_sample_site():
    p = analyze_website(base_url() + "/index.html", **LOOPBACK)
    assert p.status_code == 200 and "Acme" in p.title and p.search_forms and len(p.images) == 3


def test_rule_generator_only_uses_whitelisted_actions():
    tests = rule_based_tests(prof())
    assert len(tests) >= 8 and all(t.action in ACTIONS for t in tests)
    assert len({t.test_id for t in tests}) == len(tests)


def test_ai_output_is_validated_never_executed():
    good = {"name": "Check form", "action": "forms", "params": {"index": 1}, "severity": "high", "steps": ["a"], "expected": "x"}
    bad = [
        {"name": "rce", "action": "exec", "params": {"code": "import os"}},
        {"name": "eval", "action": "__import__('os').system", "params": {}},
        {"name": "bad param", "action": "buttons", "params": {"selector": "body"}},
        {"name": "oob", "action": "forms", "params": {"index": 99}},
        {"name": "huge", "action": "broken_links", "params": {"max_links": 100000}},
        "not a dict", {"no": "action"},
    ]
    ok, rejected = validate_ai_tests({"tests": [good] + bad}, prof(), start=10)
    assert [t.name for t in ok] == ["Check form"] and ok[0].severity == "HIGH" and ok[0].test_id.startswith("AI-")
    assert len(rejected) >= 5
