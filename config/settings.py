"""Central configuration. Secrets come from environment / .env only and are never logged."""
from __future__ import annotations

import json
import os
from pathlib import Path

try:  # python-dotenv is optional at import time
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(BASE_DIR / ".env")

SCREENSHOT_DIR = BASE_DIR / "screenshots"
RESULTS_DIR = BASE_DIR / "test_results"
DB_PATH = Path(os.getenv("TESTPILOT_DB", RESULTS_DIR / "testpilot.db"))
USER_SETTINGS_FILE = RESULTS_DIR / "user_settings.json"
APP_NAME = "TestPilot AI"
APP_TAGLINE = "Automated Testing Agent"
VERSION = "1.1.0"

for _d in (SCREENSHOT_DIR, RESULTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(v: str | None, default: int) -> int:
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        return default


DEFAULTS = {
    "allow_loopback": _bool(os.getenv("ALLOW_LOOPBACK_TARGETS"), False),
    "allow_private": _bool(os.getenv("ALLOW_PRIVATE_NETWORKS"), False),
    "headless": _bool(os.getenv("HEADLESS_BROWSER"), True),
    "request_timeout": _int(os.getenv("REQUEST_TIMEOUT"), 10),
    "page_load_timeout": _int(os.getenv("PAGE_LOAD_TIMEOUT"), 30),
    "max_links": _int(os.getenv("MAX_LINKS_TO_CHECK"), 25),
    "render_wait": _int(os.getenv("RENDER_WAIT_SECONDS"), 8),  # max seconds to let JavaScript apps render
    "use_ai": True,
}


def load_user_settings() -> dict:
    """Defaults < env < saved user settings (non-secret values only)."""
    s = dict(DEFAULTS)
    try:
        if USER_SETTINGS_FILE.exists():
            s.update({k: v for k, v in json.loads(USER_SETTINGS_FILE.read_text()).items() if k in DEFAULTS})
    except Exception:
        pass
    return s


def save_user_settings(values: dict) -> None:
    clean = {k: v for k, v in values.items() if k in DEFAULTS}
    USER_SETTINGS_FILE.write_text(json.dumps(clean, indent=2))


def get_secret(name: str, default: str | None = None) -> str | None:
    """Read a configuration secret.

    Lookup order: 1) process environment (this includes values loaded from a local .env file),
    2) Streamlit secrets (`st.secrets`, used on Streamlit Community Cloud or via .streamlit/secrets.toml).
    Never raises: a missing secrets file or a non-Streamlit context simply yields `default`.
    """
    v = os.getenv(name)
    if v is not None and v.strip():
        return v.strip()
    try:
        import streamlit as st  # imported lazily: core code must work without Streamlit

        v = st.secrets.get(name)
        if v is not None and str(v).strip():
            return str(v).strip()
    except Exception:  # no secrets file, streamlit not installed, or not running under streamlit
        pass
    return default


def ai_provider() -> str | None:
    """Return 'anthropic' / 'openai' when a key is configured (env, .env or st.secrets), else None."""
    pref = (get_secret("AI_PROVIDER") or "").lower()
    has_a, has_o = bool(get_secret("ANTHROPIC_API_KEY")), bool(get_secret("OPENAI_API_KEY"))
    if pref == "anthropic" and has_a:
        return "anthropic"
    if pref == "openai" and has_o:
        return "openai"
    if has_a:
        return "anthropic"
    if has_o:
        return "openai"
    return None
