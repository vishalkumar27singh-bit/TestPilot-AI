"""Decide how to launch Chrome on each platform. Pure functions (no Selenium import) so they are unit-testable.

* macOS (Apple Silicon or Intel): use the installed Google Chrome; Selenium Manager fetches the matching driver.
  No container flags are added.
* Linux / Streamlit Community Cloud: use the system Chromium + chromedriver installed through packages.txt,
  always headless, with the sandbox/shm flags containers require.
Override with CHROME_BINARY / CHROMEDRIVER_PATH (env, .env or Streamlit secrets).
"""
from __future__ import annotations

import os
import platform as _platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable

from config.settings import get_secret

LINUX_BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
LINUX_BROWSER_PATHS = ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable")
LINUX_DRIVER_PATHS = ("/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver", "/usr/lib/chromium-browser/chromedriver", "/usr/local/bin/chromedriver")
MAC_BROWSER_PATHS = (
    os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium")


@dataclass
class BrowserConfig:
    platform: str
    headless: bool
    binary: str | None = None   # browser binary (explicit, or discovered for display)
    binary_explicit: bool = False  # True when the user forced it via CHROME_BINARY (always passed to Chrome)
    driver: str | None = None   # explicit chromedriver (None = Selenium Manager)
    args: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _first(paths, exists: Callable[[str], bool]):
    return next((p for p in paths if exists(p)), None)


def resolve_browser_config(headless: bool = True, platform: str | None = None, getenv: Callable[[str], str | None] = get_secret,
                           which: Callable[[str], str | None] = shutil.which, exists: Callable[[str], bool] = os.path.exists,
                           has_display: bool | None = None) -> BrowserConfig:
    platform = platform or sys.platform
    linux = platform.startswith("linux")
    mac = platform == "darwin"
    if has_display is None:
        has_display = bool(os.environ.get("DISPLAY"))
    cfg = BrowserConfig(platform=platform, headless=headless)
    cfg.binary = getenv("CHROME_BINARY") or None
    cfg.binary_explicit = bool(cfg.binary)
    cfg.driver = getenv("CHROMEDRIVER_PATH") or None

    if linux:
        if not cfg.binary:
            cfg.binary = next((which(n) for n in LINUX_BROWSERS if which(n)), None) or _first(LINUX_BROWSER_PATHS, exists)
        if not cfg.driver:
            cfg.driver = which("chromedriver") or _first(LINUX_DRIVER_PATHS, exists)
        if not headless or not has_display:
            if not headless:
                cfg.notes.append("No display available on Linux: forcing headless mode.")
            cfg.headless = True
        cfg.args += ["--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox"]
    elif mac and not cfg.binary:
        cfg.binary = _first(MAC_BROWSER_PATHS, exists)  # informational; Selenium also finds Chrome itself

    if cfg.headless:
        cfg.args.append("--headless=new")
    cfg.args += ["--window-size=1366,900", "--disable-gpu", "--no-first-run", "--disable-extensions", "--log-level=3", "--hide-scrollbars"]
    return cfg


def describe_missing(cfg: BrowserConfig) -> str:
    if cfg.platform.startswith("linux") and not cfg.binary:
        return ("No Chromium/Chrome binary found. On Streamlit Community Cloud add a packages.txt file containing "
                "'chromium' and 'chromium-driver' (included in this project) and reboot the app; locally on Linux install "
                "chromium, or set CHROME_BINARY.")
    if cfg.platform == "darwin" and not cfg.binary:
        return "Google Chrome was not found in /Applications. Install it from https://www.google.com/chrome/ or set CHROME_BINARY."
    where = f" (found at {cfg.binary})" if cfg.binary else ""
    if cfg.platform == "darwin":
        return (f"Chrome{where} was found but the driver could not start it. This is usually a Chrome/chromedriver version mismatch: update Chrome, "
                "remove any old 'chromedriver' from your PATH (e.g. 'brew uninstall chromedriver') so Selenium Manager can download the matching "
                "driver, and make sure you are online the first time. If it persists, run: python selfcheck_browser.py")
    return (f"Chromium/Chrome{where} was found but the driver could not start it. The browser and chromedriver versions must match "
            "(on Streamlit Cloud both come from the same apt packages: 'chromium' and 'chromium-driver').")


def install_command() -> str:
    """The exact command that installs the requirements into THE Python that is running TestPilot."""
    return f"{sys.executable} -m pip install -r requirements.txt"


def browser_version(binary: str | None) -> str:
    """Return e.g. 'Google Chrome 131.0.6778.86' or '' (never raises)."""
    if not binary:
        return ""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=8).stdout.strip()
        return out.splitlines()[0] if out else ""
    except Exception:
        return ""


def diagnose(headless: bool = True) -> dict:
    """Everything needed to understand why browser testing is or is not available. Never raises."""
    cfg = resolve_browser_config(headless)
    try:
        import selenium
        sel = getattr(selenium, "__version__", "installed")
    except Exception:
        sel = ""
    return {
        "python": sys.executable, "python_version": sys.version.split()[0], "platform": cfg.platform, "arch": _platform.machine(),
        "selenium": sel, "browser_binary": cfg.binary or "", "browser_version": browser_version(cfg.binary),
        "driver": cfg.driver or "Selenium Manager (downloads the matching chromedriver automatically on first use)",
        "headless": cfg.headless, "ready": bool(sel and cfg.binary) or bool(sel and cfg.platform == "darwin"),
        "install_command": install_command(),
    }
