"""Deployment-readiness tests: st.secrets support, browser launch configuration, graceful fallback, deployment files.
Everything here runs without Chrome, Selenium or Streamlit installed (fakes are injected and always restored)."""
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path

from config.settings import ai_provider, get_secret
from core import ai_engine
from core.test_executor import run_website_test
from testing.browser_config import describe_missing, resolve_browser_config
from testing.context import BrowserSession, BrowserUnavailable
from tests.helpers import ROOT, base_url, temp_db
from utils.helpers import fallback_notice

KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AI_PROVIDER", "AI_MODEL", "CHROME_BINARY", "CHROMEDRIVER_PATH")


@contextmanager
def env(**kv):
    """Clean secret-related env vars, apply kv, restore afterwards."""
    saved = {k: os.environ.get(k) for k in KEYS}
    for k in KEYS:
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in kv.items() if v is not None})
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


@contextmanager
def fake_streamlit(secrets):
    old = sys.modules.get("streamlit", "__absent__")
    m = types.ModuleType("streamlit")
    m.secrets = secrets
    sys.modules["streamlit"] = m
    try:
        yield
    finally:
        if old == "__absent__":
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = old


class ExplodingSecrets:
    """Mimics st.secrets when no secrets.toml exists."""
    def get(self, *a, **k):
        raise FileNotFoundError("No secrets found")


# ---------------- secrets
def test_no_secret_anywhere_means_ai_disabled():
    with env(), fake_streamlit({}):
        assert get_secret("ANTHROPIC_API_KEY") is None and ai_provider() is None and not ai_engine.is_enabled()


def test_st_secrets_enable_ai():
    with env(), fake_streamlit({"ANTHROPIC_API_KEY": "sk-from-secrets", "AI_MODEL": "my-model"}):
        assert get_secret("ANTHROPIC_API_KEY") == "sk-from-secrets"
        assert ai_provider() == "anthropic" and ai_engine.is_enabled()
        assert ai_engine.status()["model"] == "my-model"


def test_openai_secret_and_provider_preference():
    with env(), fake_streamlit({"OPENAI_API_KEY": "sk-o", "ANTHROPIC_API_KEY": "sk-a", "AI_PROVIDER": "openai"}):
        assert ai_provider() == "openai"


def test_env_and_dotenv_take_precedence_over_st_secrets():
    with env(ANTHROPIC_API_KEY="from-env"), fake_streamlit({"ANTHROPIC_API_KEY": "from-secrets"}):
        assert get_secret("ANTHROPIC_API_KEY") == "from-env"


def test_missing_secrets_file_and_blank_values_are_safe():
    with env(), fake_streamlit(ExplodingSecrets()):
        assert get_secret("ANTHROPIC_API_KEY", "dflt") == "dflt" and ai_provider() is None
    with env(), fake_streamlit({"ANTHROPIC_API_KEY": "   "}):
        assert ai_provider() is None
    with env(ANTHROPIC_API_KEY="  "), fake_streamlit({}):
        assert ai_provider() is None


def test_api_call_uses_key_from_st_secrets_and_never_leaks_it():
    import requests
    seen = {}

    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"content": [{"type": "text", "text": "hello"}]}

    def fake_post(url, headers=None, **k):
        seen["key"], seen["url"] = headers.get("x-api-key"), url
        return R()

    orig = requests.post
    requests.post = fake_post
    try:
        with env(), fake_streamlit({"ANTHROPIC_API_KEY": "sk-secret-xyz"}):
            assert ai_engine.complete("s", "p") == "hello"
            assert seen["key"] == "sk-secret-xyz" and "anthropic.com" in seen["url"]

            def boom(url, headers=None, **k):
                raise requests.ConnectionError("failed " + headers["x-api-key"])
            requests.post = boom
            try:
                ai_engine.complete("s", "p")
                raise AssertionError("expected AIUnavailable")
            except ai_engine.AIUnavailable as e:
                assert "sk-secret-xyz" not in str(e)
    finally:
        requests.post = orig


# ---------------- browser configuration
def _which(found):
    return lambda n: f"/usr/bin/{n}" if n in found else None


def test_linux_cloud_config_uses_system_chromium_headless_no_sandbox():
    c = resolve_browser_config(True, "linux", lambda k: None, _which({"chromium", "chromedriver"}), lambda p: False, has_display=False)
    assert c.binary == "/usr/bin/chromium" and c.driver == "/usr/bin/chromedriver" and c.headless
    assert {"--no-sandbox", "--disable-dev-shm-usage", "--headless=new"} <= set(c.args)


def test_linux_without_display_forces_headless_even_if_user_disabled_it():
    c = resolve_browser_config(False, "linux", lambda k: None, _which({"chromium"}), lambda p: False, has_display=False)
    assert c.headless and "--headless=new" in c.args and c.notes


def test_linux_falls_back_to_known_paths_and_env_override_wins():
    c = resolve_browser_config(True, "linux", lambda k: None, lambda n: None, lambda p: p in ("/usr/bin/chromium", "/usr/bin/chromedriver"), False)
    assert c.binary == "/usr/bin/chromium" and c.driver == "/usr/bin/chromedriver"
    o = resolve_browser_config(True, "linux", {"CHROME_BINARY": "/opt/c", "CHROMEDRIVER_PATH": "/opt/d"}.get, _which({"chromium"}), lambda p: False, False)
    assert (o.binary, o.driver) == ("/opt/c", "/opt/d")


def test_macos_config_is_unchanged_selenium_manager_no_container_flags():
    c = resolve_browser_config(True, "darwin", lambda k: None, lambda n: None, lambda p: True, has_display=True)
    assert c.driver is None  # Selenium Manager picks the right (arm64) chromedriver
    assert "--no-sandbox" not in c.args and "--disable-dev-shm-usage" not in c.args and "--headless=new" in c.args
    visible = resolve_browser_config(False, "darwin", lambda k: None, lambda n: None, lambda p: True, True)
    assert not visible.headless and "--headless=new" not in visible.args


def test_missing_browser_message_points_to_packages_txt():
    c = resolve_browser_config(True, "linux", lambda k: None, lambda n: None, lambda p: False, False)
    assert c.binary is None and "packages.txt" in describe_missing(c) and "chromium-driver" in describe_missing(c)


# ---------------- graceful fallback
def test_selenium_package_missing_gives_clear_error():
    old = sys.modules.get("selenium", "__absent__")
    sys.modules["selenium"] = None  # makes `import selenium` raise ImportError
    try:
        BrowserSession().start()
        raise AssertionError("expected BrowserUnavailable")
    except BrowserUnavailable as e:
        assert "selenium" in str(e).lower() and "not installed" in str(e).lower()
    finally:
        if old == "__absent__":
            sys.modules.pop("selenium", None)
        else:
            sys.modules["selenium"] = old


def test_run_falls_back_to_static_engine_with_clear_log_and_notice():
    import core.test_executor as ex

    class NoBrowser:
        def __init__(self, *a, **k): pass
        def start(self): raise BrowserUnavailable("No Chromium/Chrome binary found (simulated).")
        def close(self): pass

    orig = ex.BrowserSession
    ex.BrowserSession = NoBrowser
    try:
        db = temp_db()
        logs = []
        rid = run_website_test(base_url() + "/index.html", {"engine": "auto", "allow_loopback": True, "use_ai": False},
                               on_log=lambda l, _: logs.append(l), db=db)
    finally:
        ex.BrowserSession = orig
    run = db.get_run(rid)
    tests = {t["name"]: t for t in db.get_test_cases(rid)}
    assert run["status"] == "COMPLETED" and run["engine"].startswith("static")
    assert any("Falling back to the static HTTP engine" in l for l in logs) and any("simulated" in l for l in logs)
    assert tests["No browser console errors"]["status"] == "SKIPPED" and tests["Website loads"]["status"] == "PASS"
    assert db.list_screenshots(rid) == []  # nothing faked
    notice = fallback_notice(run)
    assert "static HTTP engine" in notice and "simulated" in notice


def test_no_fallback_notice_when_static_was_chosen_deliberately():
    db = temp_db()
    rid = run_website_test(base_url() + "/index.html", {"engine": "static", "allow_loopback": True, "use_ai": False}, db=db)
    assert fallback_notice(db.get_run(rid)) == ""


# ---------------- deployment files
def test_deployment_files_present_and_correct():
    pk = (ROOT / "packages.txt").read_text().split()
    assert "chromium" in pk and "chromium-driver" in pk
    req = (ROOT / "requirements.txt").read_text().lower()
    for pkg in ("streamlit", "selenium", "plotly", "reportlab", "requests", "beautifulsoup4", "pandas", "lxml", "pytest"):
        assert pkg in req, pkg
    gi = (ROOT / ".gitignore").read_text()
    assert ".env" in gi and ".streamlit/secrets.toml" in gi
    assert (ROOT / "app.py").exists() and (ROOT / ".streamlit" / "secrets.toml.example").exists()
    assert not (ROOT / ".env").exists() and not (ROOT / ".streamlit" / "secrets.toml").exists()
