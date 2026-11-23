"""SSRF protection and safe HTTP access.

Every URL the platform touches (target site, discovered links, API endpoints) passes through
`validate_url`. Redirects are followed manually so each hop is re-validated.

Known limitation: DNS is resolved once for validation and again by the HTTP client, so a hostile
DNS server could theoretically rebind between the two lookups (TOCTOU). Only test sites you trust.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

USER_AGENT = "TestPilotAI/1.0 (+automated-testing; contact site owner)"
BLOCKED_SUFFIXES = (".local", ".internal", ".localdomain", ".lan", ".home", ".corp", ".intranet")
MAX_BYTES = 5 * 1024 * 1024


class UnsafeTargetError(ValueError):
    """The URL is malformed or points somewhere that is not allowed."""


@dataclass
class ValidatedTarget:
    url: str
    host: str
    port: int
    ips: list[str]
    loopback: bool = False


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip().strip("\"'<>").strip()
    if not raw:
        raise UnsafeTargetError("Please enter a URL.")
    if " " in raw:
        raise UnsafeTargetError("URL must not contain spaces.")
    if "://" not in raw:
        raw = "https://" + raw
    return raw


def _classify(ip_str: str) -> str:
    """Return 'public', 'loopback' or 'blocked' for an IP."""
    ip = ipaddress.ip_address(ip_str.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "loopback"
    if ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified or not ip.is_global:
        return "blocked"
    return "public"


def validate_url(raw: str, allow_loopback: bool = False, allow_private: bool = False) -> ValidatedTarget:
    url = normalize_url(raw)
    try:
        p = urlparse(url)
        port = p.port
    except ValueError:
        raise UnsafeTargetError("That URL is not valid.")
    if p.scheme not in ("http", "https"):
        raise UnsafeTargetError("Only http:// and https:// URLs are allowed.")
    if p.username or p.password:
        raise UnsafeTargetError("URLs with embedded credentials are not allowed.")
    host = (p.hostname or "").strip(".").lower()
    if not host or "." not in host and host != "localhost" and ":" not in host and not allow_private:
        if host != "localhost":
            raise UnsafeTargetError("Enter a full domain name such as example.com.")
    if host == "localhost" or host.endswith(BLOCKED_SUFFIXES):
        if not (host == "localhost" and allow_loopback):
            raise UnsafeTargetError("Local and internal hostnames are blocked by default.")
    port = port or (443 if p.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeTargetError(f"Could not resolve '{host}'. Check the spelling and your connection.")
    ips = sorted({i[4][0] for i in infos})
    if not ips:
        raise UnsafeTargetError(f"'{host}' did not resolve to any address.")
    all_loopback = True
    for ip in ips:
        kind = _classify(ip)
        if kind == "loopback":
            if not allow_loopback:
                raise UnsafeTargetError("Loopback/localhost targets are blocked. Enable 'Allow localhost' in Settings only for your own demo sites.")
        elif kind == "blocked":
            if not allow_private:
                raise UnsafeTargetError(f"'{host}' resolves to a private/internal address ({ip}); this is blocked to prevent SSRF.")
            all_loopback = False
        else:
            all_loopback = False
    return ValidatedTarget(url=url, host=host, port=port, ips=ips, loopback=all_loopback)


class RedirectError(requests.exceptions.TooManyRedirects):
    """Redirect loop / too many redirects. NOT a security block and NOT proof the site is down:
    the server did answer. `chain` holds the validated (status, url) hops that were followed."""

    def __init__(self, message, chain=None, loop=False):
        super().__init__(message)
        self.chain, self.loop = list(chain or []), loop

    @property
    def last_status(self) -> int:
        return self.chain[-1][0] if self.chain else 0

    @property
    def last_url(self) -> str:
        return self.chain[-1][1] if self.chain else ""  # a URL that was validated and actually requested


SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"}
DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple:
    p = urlparse(url)
    return (p.scheme, (p.hostname or "").lower(), p.port or DEFAULT_PORTS.get(p.scheme))


def safe_request(method: str, url: str, *, allow_loopback=False, allow_private=False, timeout=10,
                 headers: dict | None = None, data=None, json_body=None, max_redirects=10,
                 stream_limit: int = MAX_BYTES) -> requests.Response:
    """HTTP request with per-hop SSRF validation, timeout and a response size cap.

    * Redirects are followed manually; every hop is validated against the SSRF rules.
    * A cookie jar lives for the duration of the call, so cookie-gated redirect flows (session cookies, auth
      hand-offs) work like they do in a browser instead of looping.
    * A loop is only declared when the same URL is revisited with the same cookies (so '/' -> '/set' -> '/' works).
    * Credentials (Authorization, Cookie, API-key headers) are dropped when a redirect leaves the original origin.
    * The returned response has `.redirect_chain` = [(status, url), ...] for diagnostics.
    """
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    hdrs.update(headers or {})
    current, method = url, method.upper()
    chain: list[tuple[int, str]] = []
    seen: set = set()
    session = requests.Session()
    t0, budget = time.monotonic(), max(30.0, timeout * 3.0)
    try:
        for _ in range(max_redirects + 1):
            if time.monotonic() - t0 > budget:
                raise RedirectError(f"Redirect chain took longer than {budget:.0f}s", chain)
            validate_url(current, allow_loopback, allow_private)
            state = (method, current, tuple(sorted(session.cookies.get_dict().items())))
            if state in seen:
                path = " -> ".join(u for _, u in chain + [(0, current)])
                raise RedirectError(f"Redirect loop detected: {path}", chain, loop=True)
            seen.add(state)
            resp = session.request(method, current, headers=hdrs, data=data, json=json_body, timeout=timeout,
                                   allow_redirects=False, stream=True)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                resp.close()
                if not loc:
                    resp.redirect_chain = chain
                    return resp
                chain.append((resp.status_code, current))
                nxt = urljoin(current, loc)
                if _origin(nxt) != _origin(current):
                    hdrs = {k: v for k, v in hdrs.items() if k.lower() not in SENSITIVE_HEADERS}
                if resp.status_code in (301, 302, 303) and method not in ("GET", "HEAD"):
                    method, data, json_body = "GET", None, None
                current = nxt
                continue
            body, size = [], 0
            for chunk in resp.iter_content(65536):
                size += len(chunk)
                if size > stream_limit:
                    break
                body.append(chunk)
            resp._content = b"".join(body)
            resp._content_consumed = True
            resp.close()
            resp.redirect_chain = chain
            return resp
        raise RedirectError(f"Too many redirects (>{max_redirects}): " + " -> ".join(u for _, u in chain[-4:]) + " ...", chain)
    finally:
        session.close()


AUTH_PATH = re.compile(r"/(log-?in|sign-?in|signin|sso|oauth2?|auth(?:orize|enticate)?|accounts?/login|-/auth)(/|$|\?)", re.I)


def _availability_notes(start_url: str, resp: requests.Response) -> list[str]:
    notes = []
    chain = getattr(resp, "redirect_chain", [])
    if chain:
        a, b = urlparse(start_url), urlparse(resp.url)
        if (a.hostname or "").lower() != (b.hostname or "").lower():
            notes.append(f"Redirected to a different host: {a.hostname} -> {b.hostname}. The final page is what gets tested.")
        if AUTH_PATH.search(urlparse(resp.url).path + "?") and not AUTH_PATH.search(a.path + "?"):
            notes.append("The URL redirected to what looks like a login/authentication page. TestPilot can only test publicly "
                         "reachable pages, so the results may describe the login page rather than the app behind it.")
    return notes


def _http_error_message(status: int) -> str:
    return {
        401: "HTTP 401: the site requires authentication; TestPilot can only test publicly reachable pages.",
        403: "HTTP 403: access was denied. The page may require login or block automated clients (bot protection).",
        404: "HTTP 404: the page was not found. Check the URL path.",
        429: "HTTP 429: the site is rate-limiting requests. Wait a while and try again.",
    }.get(status, f"HTTP {status}: the server reported an error." if status >= 500 else f"HTTP {status}")


BROWSER_LIKE = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}
UNRESOLVED_NOTE = ("The server answered, but its redirects could not be followed to a final page by a plain HTTP client "
                   "(typically a cookie/JavaScript sign-in or app wake-up handshake, or a loop that depends on browser behaviour). "
                   "That does not mean the site is down: a real browser may load it, so the browser engine is used when available. "
                   "Without a browser only limited checks can run.")


def check_availability(url: str, allow_loopback=False, allow_private=False, timeout=10) -> dict:
    """Return {ok, status, time, final_url, error, redirects, notes, redirect_unresolved}. Never raises.

    Redirects are followed generically (cookie jar, up to 10 hops). If that fails with a loop / too many hops, one retry
    is made with browser-like request headers (up to 20 hops, as browsers allow). If redirects still cannot be resolved
    but the server did answer, the site is reported REACHABLE (`ok=True`, `redirect_unresolved=True`) with the last real
    status and URL, never 'unavailable'. Genuine failures (DNS, refused, timeout, TLS, HTTP 4xx/5xx, blocked targets) still are.
    """
    t0 = time.perf_counter()
    out = {"ok": False, "status": 0, "time": 0.0, "final_url": url, "error": "", "redirects": [], "notes": [], "redirect_unresolved": False}
    allow = dict(allow_loopback=allow_loopback, allow_private=allow_private, timeout=timeout)
    try:
        r, last_err, retry_note = None, None, ""
        for hdrs, hops, note in (({}, 10, ""), (BROWSER_LIKE, 20, "Redirects resolved after retrying with browser-like request headers.")):
            try:
                r = safe_request("GET", url, headers=hdrs, max_redirects=hops, **allow)
                retry_note = note
                break
            except RedirectError as e:
                last_err = e
        if r is not None:
            out.update(status=r.status_code, time=time.perf_counter() - t0, final_url=r.url, redirects=getattr(r, "redirect_chain", []))
            out["ok"] = r.status_code < 400
            out["error"] = "" if out["ok"] else _http_error_message(r.status_code)
            out["notes"] = _availability_notes(url, r) + ([retry_note] if retry_note else [])
        elif last_err is not None and last_err.chain:
            out.update(ok=True, redirect_unresolved=True, status=last_err.last_status, final_url=last_err.last_url or url,
                       redirects=last_err.chain, time=time.perf_counter() - t0, notes=[UNRESOLVED_NOTE])
        else:
            out["error"] = str(last_err or "Redirect handling failed.")
    except UnsafeTargetError as e:
        out["error"] = str(e)
    except requests.exceptions.SSLError:
        out["error"] = "SSL/TLS certificate problem."
    except requests.exceptions.Timeout:
        out.update(time=timeout, error=f"Timed out after {timeout}s.")
    except requests.exceptions.ConnectionError:
        out["error"] = "Connection failed: the server refused or dropped the connection (is the site up, and is the URL correct?)."
    except requests.exceptions.RequestException as e:
        out["error"] = f"Connection failed: {type(e).__name__}"
    return out
