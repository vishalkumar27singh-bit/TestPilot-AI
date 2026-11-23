"""Broken-link checking with SSRF-safe requests and bounded concurrency."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import requests

from core.models import TestFailure, TestSkipped
from core.safety import UnsafeTargetError

SOFT_CODES = {401, 403, 429, 999, 503}  # commonly bot-protection responses on external sites


def check_url(ctx, url: str, internal: bool) -> tuple[str, str]:
    """Return (verdict, detail) where verdict in ok|soft|broken|blocked."""
    try:
        r = ctx.http("HEAD", url, stream_limit=1024)
        if r.status_code in (400, 403, 404, 405, 501) or r.status_code >= 500:
            r = ctx.http("GET", url, stream_limit=65536)  # some servers reject HEAD
        c = r.status_code
        if c < 400:
            return "ok", f"HTTP {c}"
        if not internal and c in SOFT_CODES:
            return "soft", f"HTTP {c} (possibly bot protection)"
        return "broken", f"HTTP {c}"
    except UnsafeTargetError as e:
        return "blocked", str(e)[:80]
    except requests.exceptions.Timeout:
        return "broken", "timeout"
    except requests.exceptions.RequestException as e:
        return "broken", type(e).__name__


def broken_links(ctx, p):
    links = ctx.profile.links
    if not links:
        raise TestSkipped("No links were detected.")
    limit = int(p.get("max_links") or ctx.settings.get("max_links", 25))
    # internal links first: they are the site owner's responsibility
    ordered = sorted(links, key=lambda l: not l["internal"])
    unique, seen = [], set()
    for l in ordered:
        if l["href"] not in seen:
            seen.add(l["href"]); unique.append(l)
    batch = unique[:limit]
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda l: check_url(ctx, l["href"], l["internal"]), batch))
    broken = [(l, d) for l, (v, d) in zip(batch, results) if v == "broken"]
    soft = sum(1 for v, _ in results if v == "soft")
    blocked = sum(1 for v, _ in results if v == "blocked")
    suffix = f" ({soft} external link(s) returned bot-protection codes, {blocked} blocked by safety rules)" if soft or blocked else ""
    if broken:
        raise TestFailure(f"{len(broken)} of {len(batch)} links are broken: " +
                          "; ".join(f"{l['href'][:70]} -> {d}" for l, d in broken[:5]) + suffix, "Request each link")
    return f"Checked {len(batch)} of {len(unique)} unique links: none broken{suffix}."


def check_link(ctx, p):
    idx = p.get("index")
    if not isinstance(idx, int) or not 0 <= idx < len(ctx.profile.links):
        raise TestSkipped("Referenced link does not exist.")
    l = ctx.profile.links[idx]
    v, d = check_url(ctx, l["href"], l["internal"])
    if v == "broken":
        raise TestFailure(f"{l['href']} -> {d}", "Request link")
    if v == "blocked":
        raise TestSkipped(d)
    return f"{l['href']} -> {d}"
