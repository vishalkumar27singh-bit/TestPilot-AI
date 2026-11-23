"""Real viewport tests: the browser viewport is genuinely resized and layout is measured (needs Selenium + Chrome)."""
from __future__ import annotations

import time

from core.models import TestFailure, TestSkipped
from testing.context import wait_for_render

PRESETS = {"desktop": (1366, 768), "tablet": (768, 1024), "mobile": (390, 844)}


def responsive(ctx, p):
    label = p.get("viewport", "mobile")
    w, h = PRESETS.get(label, (int(p.get("width", 390)), int(p.get("height", 844))))
    ctx.require_browser(f"The {label} ({w}x{h}) layout check")
    b = ctx.browser
    try:
        ctx.ensure_page()
        method, iw, ih = b.set_viewport(w, h)
        if abs(iw - w) > 2:
            raise TestSkipped(f"Could not give the browser a {w}px-wide viewport (got {iw}px via {method}), so a valid {label} test is impossible.")
        time.sleep(0.2)  # let media queries / reflow apply
        wait_for_render(ctx.driver, 2.0, settle=min(b.settle, 0.5), poll=b.poll)
        m = ctx.driver.execute_script("return {sw: document.documentElement.scrollWidth, iw: window.innerWidth}")
        ctx.shot(f"{label}_{w}x{h}")
        if m["sw"] > m["iw"] + 2:
            raise TestFailure(f"Horizontal overflow at {label} size: content is {m['sw']}px wide inside a {m['iw']}px viewport (users must scroll sideways).",
                              f"Resize viewport to {w}x{h}")
        return f"Real browser viewport {iw}x{ih} ({method}): no horizontal overflow (content {m['sw']}px wide)."
    finally:
        b.reset_viewport()
