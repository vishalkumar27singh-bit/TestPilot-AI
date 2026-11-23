"""SIMULATED browser + Selenium stubs. THIS IS NOT A REAL BROWSER.

It exists only so the browser-path *logic* of TestPilot (handlers, labelling, SKIPPED reasons, viewport handling, safe-click rules,
screenshot plumbing, launch options) can be unit-tested on machines without Chrome. It fetches pages over HTTP, parses them with lxml,
does not run JavaScript or CSS, and its 'screenshots' are tiny synthetic PNGs. Real-browser behaviour is covered by tests/test_browser_real.py
(auto-skipped without Chrome) and by selfcheck_browser.py.
"""
import re
import struct
import sys
import time
import types
import zlib
from contextlib import contextmanager
from urllib.parse import urlencode, urljoin

import requests
from lxml import html as LH

from testing.context import BrowserSession

ENTER = "\ue007"


def _unesc(s): return re.sub(r"\\(.)", r"\1", s)
def _lit(s): return "'" + s.replace("'", "") + "'"


def css_to_xpath(css: str, relative: bool = False) -> str:
    """Tiny CSS subset used by TestPilot locators: tag#id .class [attr] [attr="v"] and descendant spaces."""
    parts, cur, depth, q = [], "", 0, None
    for ch in css:
        if q:
            cur += ch
            q = None if ch == q else q
            continue
        if ch in "\"'":
            q = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == " " and depth == 0:
            if cur: parts.append(cur); cur = ""
            continue
        cur += ch
    if cur:
        parts.append(cur)
    xp = "." if relative else ""
    for part in parts:
        m = re.match(r"^([a-zA-Z][\w-]*|\*)?", part)
        tag, rest, preds = m.group(1) or "*", part[m.end():], []
        for t in re.finditer(r'#((?:\\.|[\w-])+)|\.((?:\\.|[\w-])+)|\[([\w-]+)(?:="([^"]*)")?\]', rest):
            if t.group(1): preds.append(f"@id={_lit(_unesc(t.group(1)))}")
            elif t.group(2): preds.append(f"contains(concat(' ',normalize-space(@class),' '),{_lit(' ' + _unesc(t.group(2)) + ' ')})")
            elif t.group(4) is not None: preds.append(f"@{t.group(3)}={_lit(t.group(4))}")
            else: preds.append(f"@{t.group(3)}")
        xp += "//" + tag + "".join(f"[{p}]" for p in preds)
    return xp


def _png(width: int, height: int = 2) -> bytes:
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeEl:
    def __init__(self, drv, node): self.drv, self.node, self.val = drv, node, None

    @property
    def text(self): return re.sub(r"\s+", " ", self.node.text_content()).strip()
    def is_displayed(self):
        st = (self.node.get("style") or "").replace(" ", "")
        return not ("display:none" in st or self.node.get("hidden") is not None or self.node.get("type") == "hidden")
    def is_enabled(self): return self.node.get("disabled") is None
    def get_attribute(self, n):
        if n == "value": return self.val if self.val is not None else (self.node.get("value") or "")
        return self.node.get(n)
    def clear(self): self.val = ""
    def send_keys(self, s):
        if s == ENTER:
            form = next(iter(self.node.iterancestors("form")), None)
            if form is not None and (form.get("method") or "get").lower() == "get":
                q = {self.node.get("name"): self.get_attribute("value")}
                return self.drv.get(urljoin(self.drv.current_url, form.get("action") or "") + "?" + urlencode(q))
            return
        cur = self.get_attribute("value")
        ml = self.node.get("maxlength")
        self.val = (cur + s)[: int(ml)] if ml else cur + s
    def find_elements(self, by, value):
        return self.drv._find(self.node, by, value, relative=True)
    def click(self):
        name = self.text or self.node.get("aria-label") or self.node.get("value") or self.node.tag
        self.drv.clicked.append(name)
        oc = self.node.get("onclick") or ""
        if "console.error(" in oc or "throw new Error" in oc:
            self.drv.logs.append({"level": "SEVERE", "message": f"simulated click error from '{name}'"})
        href = self.node.get("href")
        if self.node.tag == "a" and href:
            self.drv.get(urljoin(self.drv.current_url, href))


class FakeDOMDriver:
    capabilities = {"browserName": "chrome", "browserVersion": "0.0-SIMULATED", "chrome": {"chromedriverVersion": "0.0-SIMULATED (x)"}}

    def __init__(self, cdp_supported=True, min_window_width=500):
        self.current_url, self.doc, self.html = "", None, ""
        self.vw, self.vh = 1366, 900
        self.logs, self.clicked, self.cdp_calls, self.history = [], [], [], []
        self.cdp_supported, self.min_window_width = cdp_supported, min_window_width
        self.window_handles = ["main"]
        self.switch_to = types.SimpleNamespace(window=lambda h: None)

    # navigation
    def get(self, url):
        r = requests.get(url, timeout=5)
        self.current_url, self.html = r.url, r.text
        self.doc = LH.fromstring(r.text) if r.text.strip() else LH.fromstring("<html></html>")
        self.history.append(url)
        for s in self.doc.xpath("//script[not(@src)]"):
            if "console.error(" in (s.text or "") or "throw new Error(" in (s.text or ""):
                self.logs.append({"level": "SEVERE", "message": f"simulated page-load error in {url}"})
    def back(self): pass
    def quit(self): pass
    def set_page_load_timeout(self, s): pass
    def set_script_timeout(self, s): pass
    @property
    def title(self): t = self.doc.xpath("//title") if self.doc is not None else []; return (t[0].text or "").strip() if t else ""
    @property
    def page_source(self): return self.html

    # DOM
    def _find(self, root, by, value, relative=False):
        xp = css_to_xpath(value, relative) if by == "css selector" else value
        if by == "xpath" and relative and not value.startswith("."): xp = "." + value
        try:
            return [FakeEl(self, n) for n in root.xpath(xp)]
        except Exception:
            return []
    def find_elements(self, by, value): return self._find(self.doc, by, value)

    def execute_script(self, js, *args):
        text = re.sub(r"\s+", " ", self.doc.text_content()).strip() if self.doc is not None else ""
        n = len(self.doc.xpath("//*")) if self.doc is not None else 0
        if "readyState" in js: return "complete"
        if "scrollIntoView" in js: return None
        if "document.images" in js:
            return [{"s": i.get("src", ""), "ok": "does-not-exist" not in i.get("src", "")} for i in self.doc.xpath("//img")]
        if "scrollWidth" in js:
            widths = [int(m) for m in re.findall(r"width:\s*(\d+)px", self.html)] or [0]
            return {"sw": max(self.vw, max(widths)), "iw": self.vw}
        if "[window.innerWidth" in js: return [self.vw, self.vh]
        if "getElementsByTagName" in js: return len(text) * 1000 + n
        if "trim().length" in js: return len(text.strip())
        raise NotImplementedError(f"simulated driver cannot run: {js[:60]}")

    # viewport
    def execute_cdp_cmd(self, cmd, params):
        self.cdp_calls.append(cmd)
        if not self.cdp_supported: raise AttributeError("no CDP")
        if cmd == "Emulation.setDeviceMetricsOverride": self.vw, self.vh = params["width"], params["height"]
        if cmd == "Emulation.clearDeviceMetricsOverride": self.vw, self.vh = 1366, 900
    def set_window_size(self, w, h): self.vw, self.vh = max(w, self.min_window_width), h
    def get_log(self, kind): out, self.logs = self.logs, []; return out
    def save_screenshot(self, path):
        open(path, "wb").write(_png(self.vw)); return True


class SimBrowserSession(BrowserSession):
    """Real BrowserSession logic (open / set_viewport / reset_viewport) running on the simulated driver."""
    drv_kwargs: dict = {}
    created: list = []

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.settle = self.poll = 0

    def start(self):
        self.driver = FakeDOMDriver(**SimBrowserSession.drv_kwargs)
        self.info = {"browser": "SIMULATED", "version": "0", "driver_version": "0", "platform": "test", "arch": "test", "headless": True}
        SimBrowserSession.created.append(self.driver)
        return self


# ---------------- selenium module stubs (for By / Keys / WebDriverWait / Chrome / Options / Service)
class By:
    CSS_SELECTOR, XPATH, ID = "css selector", "xpath", "id"


class Keys:
    ENTER = ENTER


class WebDriverWait:
    def __init__(self, driver, timeout): self.d, self.t = driver, timeout
    def until(self, cond):
        end = time.monotonic() + min(self.t, 1)
        while time.monotonic() < end:
            if cond(self.d): return True
            time.sleep(0.05)
        raise Exception("simulated timeout")


class StubOptions:
    def __init__(self): self.arguments, self.caps, self.binary_location, self.page_load_strategy = [], {}, None, None
    def add_argument(self, a): self.arguments.append(a)
    def set_capability(self, k, v): self.caps[k] = v


class StubService:
    def __init__(self, executable_path=None): self.executable_path = executable_path


def _mod(name, **attrs):
    m = types.ModuleType(name); m.__path__ = []; m.__dict__.update(attrs); return m


@contextmanager
def selenium_stub(chrome=None):
    """Install fake `selenium.*` modules; `chrome(service=..., options=...)` builds the fake driver (or raises)."""
    launched = {}

    def Chrome(service=None, options=None):
        launched.update(service=service, options=options)
        if chrome: return chrome(service, options)
        return FakeDOMDriver()

    names = ["selenium", "selenium.webdriver", "selenium.webdriver.common", "selenium.webdriver.common.by", "selenium.webdriver.common.keys",
             "selenium.webdriver.support", "selenium.webdriver.support.ui", "selenium.webdriver.chrome", "selenium.webdriver.chrome.options",
             "selenium.webdriver.chrome.service"]
    mods = {"selenium": _mod("selenium", __version__="4.99.0-stub"), "selenium.webdriver": _mod("selenium.webdriver", Chrome=Chrome),
            "selenium.webdriver.common": _mod("selenium.webdriver.common"), "selenium.webdriver.common.by": _mod("by", By=By),
            "selenium.webdriver.common.keys": _mod("keys", Keys=Keys), "selenium.webdriver.support": _mod("support"),
            "selenium.webdriver.support.ui": _mod("ui", WebDriverWait=WebDriverWait), "selenium.webdriver.chrome": _mod("chrome"),
            "selenium.webdriver.chrome.options": _mod("options", Options=StubOptions), "selenium.webdriver.chrome.service": _mod("service", Service=StubService)}
    mods["selenium"].webdriver = mods["selenium.webdriver"]
    old = {n: sys.modules.get(n) for n in names}
    sys.modules.update(mods)
    try:
        yield launched
    finally:
        for n, v in old.items():
            if v is None: sys.modules.pop(n, None)
            else: sys.modules[n] = v
