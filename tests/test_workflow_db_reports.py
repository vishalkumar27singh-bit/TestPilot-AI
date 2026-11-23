import csv
import io
import json

from core import ai_engine
from core.test_executor import RunAborted, run_api_test, run_website_test
from reports.report_generator import generate
from tests.helpers import base_url, temp_db

CFG = {"engine": "static", "allow_loopback": True, "use_ai": False}


def _run(db, path="/index.html"):
    return run_website_test(base_url() + path, CFG, db=db)


def test_no_ai_key_means_fallback_mode():
    import os
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(k, None)
    assert not ai_engine.is_enabled()


def test_full_workflow_produces_real_results_and_bugs():
    db = temp_db()
    rid = _run(db)
    run = db.get_run(rid)
    tests = db.get_test_cases(rid)
    assert run["status"] == "COMPLETED" and run["total"] == len(tests) >= 10
    by = {t["name"]: t for t in tests}
    assert by["No broken links"]["status"] == "FAIL" and "missing-page" in by["No broken links"]["actual"]
    assert by["Images have alt text"]["status"] == "FAIL"
    assert by["Website loads"]["status"] == "PASS" and by["Page title present"]["status"] == "PASS"
    bugs = db.list_bugs(rid)
    assert len(bugs) == run["failed"] > 0 and all(b["suggested_solution"] for b in bugs)
    assert all(t["probable_cause"] for t in tests if t["status"] == "FAIL")


def test_clean_page_has_no_failures():
    db = temp_db()
    rid = _run(db, "/products.html")
    assert db.get_run(rid)["failed"] == 0


def test_unsafe_and_unreachable_targets_abort_cleanly():
    db = temp_db()
    for url, cfg in (("http://169.254.169.254/", CFG), ("http://127.0.0.1:9/", CFG)):
        try:
            run_website_test(url, cfg, db=db)
            raise AssertionError("should have aborted")
        except RunAborted:
            pass
    try:
        run_website_test(base_url(), {**CFG, "allow_loopback": False}, db=db)
        raise AssertionError("loopback must be blocked by default")
    except RunAborted:
        pass


def test_history_compare_and_delete_cascade():
    db = temp_db()
    a, b = _run(db), _run(db, "/products.html")
    cmp = db.compare_runs(a, b)
    assert cmp and {"test", "change"} <= set(cmp[0])
    db.delete_run(a)
    assert db.get_run(a) is None and db.get_test_cases(a) == [] and db.list_bugs(a) == []
    assert db.get_run(b) is not None


def test_all_report_formats():
    db = temp_db()
    rid = _run(db)
    pdf, _, _ = generate(rid, "pdf", db)
    assert pdf.startswith(b"%PDF")
    rows = list(csv.DictReader(io.StringIO(generate(rid, "csv", db)[0].decode("utf-8-sig"))))
    assert rows and rows[0]["status"] in ("PASS", "FAIL", "SKIPPED")
    data = json.loads(generate(rid, "json", db)[0])
    assert data["summary"]["total"] == len(rows) and data["failed_tests"]
    html = generate(rid, "html", db)[0].decode()
    assert "<script" not in html.lower() and "TestPilot AI" in html


def test_html_report_escapes_hostile_content():
    db = temp_db()
    rid = db.create_run("http://x.test", "ui")
    from core.models import TestCase
    tc = TestCase("T-1", "<script>alert(1)</script>", "c", "o", ["s"], "e", "page_load", status="FAIL", actual="<img src=x onerror=alert(1)>", severity="HIGH")
    db.add_test_cases(rid, [tc]); db.finish_run(rid, "COMPLETED", 1.0, [tc])
    html = generate(rid, "html", db)[0].decode()
    assert "<script>alert" not in html and "&lt;script&gt;" in html


def test_api_run_saved_with_bug_for_failure():
    db = temp_db()
    rid = run_api_test(base_url() + "/api/broken", config={"allow_loopback": True, "use_ai": False}, db=db)
    assert db.get_run(rid)["kind"] == "api" and db.get_run(rid)["failed"] == 1 and len(db.list_bugs(rid)) == 1
