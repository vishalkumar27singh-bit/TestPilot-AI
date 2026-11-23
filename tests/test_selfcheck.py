"""The real-browser tests must SKIP (never pass) when no browser exists, and the self-check must say so."""
import io
import sys
import unittest
from contextlib import redirect_stdout

import tests.test_browser_real as real
from testing.context import BrowserUnavailable


class NoChrome:
    def __init__(self, *a, **k): pass
    def start(self): raise BrowserUnavailable("simulated: no Chrome here")


def test_real_browser_tests_skip_without_a_browser_and_selfcheck_exits_2():
    import selfcheck_browser
    orig = real.BrowserSession
    real.BrowserSession = NoChrome
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = selfcheck_browser.main()
    finally:
        real.BrowserSession = orig
        real._state.clear()
    out = buf.getvalue()
    assert code == 2 and "NO REAL BROWSER AVAILABLE" in out and "simulated: no Chrome here" in out
    assert "[PASS]" not in out and out.count("[SKIP]") >= 10  # every real-browser test skipped; none passed without a browser


def test_a_real_test_raises_skip_not_pass_when_browser_missing():
    orig = real.BrowserSession
    real.BrowserSession = NoChrome
    real._state.clear()
    try:
        real.test_real_title_and_page_load_pass_in_the_browser()
        raise AssertionError("must not pass without a browser")
    except unittest.SkipTest as e:
        assert "real browser unavailable" in str(e)
    finally:
        real.BrowserSession = orig
        real._state.clear()
