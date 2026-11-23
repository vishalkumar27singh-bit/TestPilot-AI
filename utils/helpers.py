from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean_text(s: str | None, limit: int = 120) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def same_site(a: str, b: str) -> bool:
    ha, hb = (urlparse(a).hostname or "").lower(), (urlparse(b).hostname or "").lower()
    strip = lambda h: h[4:] if h.startswith("www.") else h
    return strip(ha) == strip(hb)


def absolute(base: str, href: str) -> str:
    return urljoin(base, href)


def xpath_literal(s: str) -> str:
    """Safely quote an arbitrary string for use inside an XPath expression."""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in s.split("'")) + ")"


def css_escape(s: str) -> str:
    return re.sub(r"([^a-zA-Z0-9_\-])", r"\\\1", s)


def slugify(s: str, n: int = 40) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:n] or "item"


def fmt_duration(sec: float | None) -> str:
    sec = sec or 0
    return f"{sec*1000:.0f} ms" if sec < 1 else (f"{sec:.1f} s" if sec < 60 else f"{int(sec//60)}m {int(sec%60)}s")


def fallback_notice(run: dict) -> str:
    """Non-empty when a browser engine was requested but the run used the static HTTP engine instead."""
    import json
    try:
        wanted = json.loads(run.get("config_json") or "{}").get("engine", "auto")
    except ValueError:
        wanted = "auto"
    if run.get("kind") != "ui" or wanted == "static" or not str(run.get("engine") or "").startswith("static"):
        return ""
    reason = ""
    for l in (run.get("log_text") or "").splitlines():
        if "Browser unavailable:" in l:
            reason = l.split("Browser unavailable:", 1)[1].strip(); break
        if "Browser could not open the page" in l:
            reason = l.split("! ", 1)[-1].split(" Falling back")[0].strip(); break
    return ("Browser testing was not available, so this run used the static HTTP engine: screenshots, console-error checks and real layout "
            "checks were skipped (shown as SKIPPED). " + (f"Reason: {reason}" if reason else "See the execution log for details."))


def urls_equivalent(a: str, b: str) -> bool:
    """Same page ignoring scheme/host case, #fragment and a trailing slash."""
    from urllib.parse import urlparse
    pa, pb = urlparse(a or ""), urlparse(b or "")
    norm = lambda p: (p.scheme.lower(), (p.hostname or "").lower(), p.port, (p.path or "/").rstrip("/") or "/", p.query)
    return norm(pa) == norm(pb)
