"""Run context shared by all test handlers + the Selenium browser wrapper."""
from __future__ import annotations

import platform as _platform
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from core.models import SiteProfile, TestSkipped
from core.safety import safe_request, validate_url
from utils.helpers import urls_equivalent
from utils.logger import RunLog


class BrowserUnavailable(RuntimeError):
    pass


def wait_for_render(driver, max_wait: float = 8.0, settle: float = 1.0, poll: float = 0.4, sleep=time.sleep, now=time.monotonic) -> bool:
    """Wait until the page is complete AND its DOM/text size stops changing (JS apps render after load).
    Returns True if the page settled, False on timeout. Never raises."""
    deadline, last, stable_since = now() + max_wait, None, None
    js = "return document.body ? (document.body.innerText.length * 1000 + document.getElementsByTagName('*').length) : 0"
    while now() < deadline:
        try:
            ready, size = driver.execute_script("return document.readyState"), driver.execute_script(js)
        except Exception:
            return False
        if ready == "complete" and size:
            if size == last:
                stable_since = stable_since if stable_since is not None else now()
                if now() - stable_since >= settle:
                    return True
            else:
                last, stable_since = size, None
        else:
            last, stable_since = None, None
        sleep(poll)
    return False


class BrowserSession:
    """Chrome via Selenium 4. Launch options are decided in testing/browser_config.py (macOS vs Linux/Cloud)."""

    def __init__(self, headless: bool = True, page_load_timeout: int = 30):
        self.headless, self.page_load_timeout = headless, page_load_timeout
        self.driver = None
        self.config = None
        self.info: dict = {}
        self.settle, self.poll = 1.0, 0.4  # render-wait tuning (tests set these to 0 for speed)

    def start(self):
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except ImportError:
            from testing.browser_config import install_command
            raise BrowserUnavailable(f"The 'selenium' package is not installed in the Python that is running TestPilot ({sys.executable}). "
                                     f"Install it into that interpreter with:  {install_command()}  then restart Streamlit "
                                     f"(tip: start the app with 'python -m streamlit run app.py' so the same interpreter is used).")
        from testing.browser_config import describe_missing, resolve_browser_config
        cfg = resolve_browser_config(self.headless)
        self.config = cfg
        opts = Options()
        if cfg.binary and (cfg.binary_explicit or cfg.platform != "darwin"):
            opts.binary_location = cfg.binary  # macOS: Selenium finds Chrome itself unless CHROME_BINARY forces a path
        for a in cfg.args:
            opts.add_argument(a)
        opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
        opts.page_load_strategy = "normal"
        try:
            service = Service(executable_path=cfg.driver) if cfg.driver else Service()
            self.driver = webdriver.Chrome(service=service, options=opts)
        except Exception as e:
            first = (str(e).strip().splitlines() or [""])[0][:160]
            raise BrowserUnavailable(f"Could not start Chrome via Selenium ({type(e).__name__}: {first}). {describe_missing(cfg)}") from None
        self.driver.set_page_load_timeout(self.page_load_timeout)
        self.driver.set_script_timeout(self.page_load_timeout)
        caps = getattr(self.driver, "capabilities", {}) or {}
        self.info = {"browser": caps.get("browserName", "chrome"), "version": caps.get("browserVersion", ""),
                     "driver_version": str((caps.get("chrome") or {}).get("chromedriverVersion", "")).split(" ")[0],
                     "platform": cfg.platform, "arch": _platform.machine(), "headless": cfg.headless}
        return self

    def open(self, url: str, render_wait: float = 8.0) -> bool:
        """Navigate and wait for client-side rendering to settle."""
        self.driver.get(url)
        return wait_for_render(self.driver, render_wait, settle=self.settle, poll=self.poll)

    def set_viewport(self, width: int, height: int) -> tuple[str, int, int]:
        """Resize the viewport for real. Prefers Chrome DevTools device-metrics emulation (exact size; Chrome refuses
        window widths below ~500px), falling back to a window resize. Returns (method, innerWidth, innerHeight)."""
        d, method = self.driver, "window resize"
        try:
            d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
            method = "DevTools device emulation"
        except Exception:
            d.set_window_size(width, height)
        iw, ih = d.execute_script("return [window.innerWidth, window.innerHeight]")
        return method, int(iw), int(ih)

    def reset_viewport(self):
        for fn in (lambda: self.driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {}), lambda: self.driver.set_window_size(1366, 900)):
            try:
                fn()
            except Exception:
                pass

    def close(self):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None


@dataclass
class RunContext:
    profile: SiteProfile
    settings: dict
    log: RunLog
    browser: BrowserSession | None = None
    screens: Any = None  # ScreenshotManager
    browser_reason: str = ""  # why there is no browser for this run (shown in SKIPPED results)
    current_test_id: str = ""
    shots: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # test_id -> [(label, path)]

    @property
    def driver(self):
        return self.browser.driver if self.browser and self.browser.driver else None

    @property
    def base_url(self) -> str:
        return self.profile.final_url or self.profile.url

    def http(self, method: str, url: str, timeout: int | None = None, **kw):
        return safe_request(method, url, allow_loopback=self.settings.get("allow_loopback", False),
                            allow_private=self.settings.get("allow_private", False),
                            timeout=timeout or self.settings.get("request_timeout", 10), **kw)

    def assert_allowed(self, url: str):
        validate_url(url, self.settings.get("allow_loopback", False), self.settings.get("allow_private", False))

    def require_browser(self, what: str = "This check"):
        """Browser-only checks are SKIPPED (never faked with static data) when no real browser is running."""
        if not self.driver:
            raise TestSkipped(f"{what} needs a real browser (Selenium + Chrome), which was not available for this run. {self.browser_reason}".strip())

    def navigate(self, url: str):
        """SSRF-checked navigation that waits for JavaScript-rendered content."""
        self.assert_allowed(url)
        if self.browser and self.browser.driver:
            self.browser.open(url, self.settings.get("render_wait", 8))
        else:
            self.driver.get(url)

    def ensure_page(self):
        """Make sure the browser is on the base page (handlers may have navigated away)."""
        if self.driver is None:
            return
        try:
            cur = self.driver.current_url
        except Exception:
            cur = ""
        if not urls_equivalent(cur, self.base_url):
            self.navigate(self.base_url)

    def shot(self, label: str) -> str:
        if not (self.driver and self.screens):
            return ""
        path = self.screens.capture(self.driver, self.current_test_id, label)
        if path:
            self.shots.setdefault(self.current_test_id, []).append((label, path))
        return path
