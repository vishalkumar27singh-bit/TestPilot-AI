"""Pytest integration: executes a prepared list of TestCases *through pytest* (parametrized, with a
reporting plugin). Used by the executor when engine option "Run through pytest" is selected.

Safety: the parametrized function only dispatches to the whitelisted handlers in testing.registry.
"""
from __future__ import annotations

import pytest

from core.models import TestFailure, TestSkipped
from testing._bridge_state import STATE as _STATE


def pytest_generate_tests(metafunc):
    if "case_id" in metafunc.fixturenames:
        ids = _STATE["ids"] or ["__none__"]
        metafunc.parametrize("case_id", ids, ids=ids)


def test_case(case_id):
    if case_id == "__none__":
        pytest.skip("no cases registered")
    try:
        _STATE["run_one"](case_id)
    except TestSkipped as e:
        pytest.skip(str(e))
    except TestFailure as e:
        pytest.fail(str(e), pytrace=False)


class _Reporter:
    def __init__(self, on_result):
        self.on_result = on_result

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or (report.when == "setup" and report.skipped):
            self.on_result(report.nodeid.split("[")[-1].rstrip("]"), report.outcome, report.duration)


def run_via_pytest(case_ids: list[str], run_one, on_result) -> int:
    """run_one(case_id) executes one test (raising TestFailure/TestSkipped). Returns pytest exit code."""
    _STATE["ids"], _STATE["run_one"] = list(case_ids), run_one
    return int(pytest.main([__file__, "-q", "-s", "-p", "no:cacheprovider", "--no-header", "-p", "no:randomly"],
                           plugins=[_Reporter(on_result)]))
