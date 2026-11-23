#!/usr/bin/env python
"""Real-browser self-check for TestPilot AI.   Usage:  python selfcheck_browser.py

Prints environment diagnostics, then drives REAL Chrome through Selenium against the bundled sample site (JavaScript rendering,
viewport resizing, screenshots, console errors, safe clicks, navigation) and reports PASS/FAIL/SKIP for each check.
Exit code: 0 = every check passed in a real browser, 1 = at least one check failed, 2 = no real browser available (nothing was verified).
"""
import sys
import time
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from testing.browser_config import diagnose
    d = diagnose()
    print("TestPilot AI - real browser self-check\n" + "=" * 60)
    print(f"Python      : {d['python']} ({d['python_version']})")
    print(f"Platform    : {d['platform']} / {d['arch']}")
    print(f"Selenium    : {d['selenium'] or 'NOT INSTALLED in this Python'}")
    print(f"Browser     : {d['browser_binary'] or 'not found'} {d['browser_version']}")
    print(f"Driver      : {d['driver']}")
    if not d["selenium"]:
        print(f"\nInstall Selenium into THIS Python:\n    {d['install_command']}")
    print("=" * 60)

    import tests.test_browser_real as real
    real._state.clear()
    passed = failed = skipped = 0
    skip_reasons = set()
    for name in sorted(n for n in dir(real) if n.startswith("test_")):
        t0 = time.time()
        try:
            getattr(real, name)()
            passed += 1
            print(f"[PASS] {name} ({time.time() - t0:.1f}s)")
        except unittest.SkipTest as e:
            skipped += 1
            skip_reasons.add(str(e))
            print(f"[SKIP] {name}")
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name} ({time.time() - t0:.1f}s)\n       {type(e).__name__}: {str(e)[:600]}")
            if "-v" in sys.argv:
                traceback.print_exc()
    print("=" * 60 + f"\nSummary: {passed} passed, {failed} failed, {skipped} skipped")
    if skipped and not passed and not failed:
        print("\nNO REAL BROWSER AVAILABLE: nothing was verified.")
        for r in skip_reasons:
            print("  reason:", r)
        return 2
    if failed:
        print("\nSome real-browser checks FAILED. Re-run with -v for tracebacks and send me this output.")
        return 1
    print("\nAll real-browser checks passed in a real Chrome.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
