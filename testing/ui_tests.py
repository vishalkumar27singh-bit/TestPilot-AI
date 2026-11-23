"""UI test handlers. Each handler: fn(ctx, params) -> actual-result string.
Raise TestFailure for a failed assertion, TestSkipped when a test is not applicable.
Handlers work with Selenium when a browser is available and fall back to static HTTP checks otherwise."""
from __future__ import annotations

import re
import time

from core.models import TestFailure, TestSkipped
from core.safety import UnsafeTargetError
from testing.context import wait_for_render

SAMPLE_VALUES = {"email": "test@example.com", "number": "42", "tel": "5551234567", "url": "https://example.com",
                 "password": "Test#1234", "search": "testpilot", "text": "TestPilot sample", "textarea": "TestPilot sample text"}


def find(ctx, locator):
    from selenium.webdriver.common.by import By
    if not locator or not locator[0]:
        return []
    by = By.CSS_SELECTOR if locator[0] == "css" else By.XPATH
    try:
        return ctx.driver.find_elements(by, locator[1])
    except Exception:
        return []


def _pick(items: list, params: dict, cap: int = 15):
    idx = params.get("index")
    if isinstance(idx, int):
        if not 0 <= idx < len(items):
            raise TestSkipped(f"Element #{idx} does not exist on the page.")
        return [(idx, items[idx])]
    return list(enumerate(items))[:cap]


def _needs_browser(ctx):
    """Redirects could not be resolved over HTTP, so without a browser there is no page to inspect: skip, never fail."""
    if not ctx.driver and ctx.profile.redirect_unresolved:
        raise TestSkipped("The site's redirects could not be resolved over plain HTTP; this check needs the browser engine.")


def page_load(ctx, p):
    _needs_browser(ctx)
    if ctx.driver:
        ctx.ensure_page()
        state = ctx.driver.execute_script("return document.readyState")
        ctx.shot("loaded")
        if state != "complete":
            raise TestFailure(f"Document readyState was '{state}' instead of 'complete'.", "Wait for page load")
        return f"Page fully loaded in browser at {ctx.driver.current_url} (readyState=complete)."
    if not (0 < ctx.profile.status_code < 400):
        raise TestFailure(f"Page returned HTTP {ctx.profile.status_code}.", "Open the URL")
    return f"Page fetched over HTTP in {ctx.profile.load_time:.2f}s ({ctx.profile.html_bytes} bytes). [static HTML/HTTP check, not run in a browser]"


def page_title(ctx, p):
    _needs_browser(ctx)
    title = (ctx.driver.title if ctx.driver else ctx.profile.title or "").strip()
    if not title:
        raise TestFailure("The page has no <title> or it is empty.", "Read page title")
    note = "" if 10 <= len(title) <= 70 else f" (note: {len(title)} characters; 10-70 is the recommended SEO range)"
    return f"Title is '{title}'{note}."


def http_availability(ctx, p):
    _needs_browser(ctx)
    if ctx.profile.redirect_unresolved:
        return "The browser loaded the page after following redirects that a plain HTTP client could not resolve (HTTP status not exposed by Selenium)."
    sc = ctx.profile.status_code
    if not 200 <= sc < 400:
        raise TestFailure(f"Server answered HTTP {sc}.", "Send GET request")
    slow = " — slower than the 3s guideline" if ctx.profile.load_time > 3 else ""
    return f"HTTP {sc} in {ctx.profile.load_time:.2f}s{slow}."


def _text(l):
    return l["text"] or l["href"]


def _close_extra_windows(ctx, handles_before):
    d = ctx.driver
    try:
        for h in d.window_handles:
            if h not in handles_before:
                d.switch_to.window(h); d.close()
        d.switch_to.window(handles_before[0])
    except Exception:
        pass


def _settle(ctx):
    wait_for_render(ctx.driver, min(ctx.settings.get("render_wait", 8), 5), settle=ctx.browser.settle, poll=ctx.browser.poll)


def navigation_links(ctx, p):
    links = ctx.profile.nav_links or [l for l in ctx.profile.links if l["internal"]]
    if not links:
        raise TestSkipped("No navigation links were detected.")
    internal = [l for l in links if l["internal"]]
    problems, ok = [], 0
    if ctx.driver:  # REAL clicks in the browser
        ctx.ensure_page()
        visible = sum(1 for l in links[:15] for e in find(ctx, l["locator"])[:1] if e.is_displayed())
        for l in internal[:3]:
            el = next((e for e in find(ctx, l["locator"]) if e.is_displayed()), None)
            if el is None or (el.get_attribute("target") or "") == "_blank":
                continue
            handles = list(ctx.driver.window_handles)
            try:
                ctx.driver.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
                el.click()
            except Exception as e:
                problems.append(f"'{_text(l)}' could not be clicked ({type(e).__name__}); it may be covered by another element")
                ctx.ensure_page(); continue
            _settle(ctx)
            _close_extra_windows(ctx, handles)
            try:
                ctx.assert_allowed(ctx.driver.current_url)
            except UnsafeTargetError:
                problems.append(f"Clicking '{_text(l)}' led to a blocked address"); ctx.ensure_page(); continue
            if not ctx.driver.execute_script("return (document.body && document.body.innerText || '').trim().length"):
                problems.append(f"Clicking '{_text(l)}' led to an empty page ({ctx.driver.current_url})")
            else:
                ok += 1
                ctx.shot(f"nav{ok}")
            ctx.ensure_page()
        if not ok and not problems:
            raise TestSkipped("No visible, clickable internal navigation links were found in the browser.")
        if problems:
            raise TestFailure("; ".join(problems), "Click navigation link")
        return f"{visible} navigation links visible; clicked {ok} internal link(s) in the real browser: each loaded a non-empty page; returned to the start page."
    for l in internal[:5]:
        try:
            r = ctx.http("GET", l["href"], stream_limit=65536)
            if r.status_code >= 400:
                problems.append(f"'{_text(l)}' -> HTTP {r.status_code}")
            else:
                ok += 1
        except Exception as e:
            problems.append(f"'{_text(l)}' -> {type(e).__name__}")
    if problems:
        raise TestFailure("; ".join(problems), "Request navigation link")
    return f"{ok} internal navigation links responded successfully. [static HTTP check, links were not clicked]"


def buttons(ctx, p):
    items = _pick(ctx.profile.buttons, p)
    if not items:
        raise TestSkipped("No buttons were detected.")
    problems, visible = [], 0
    if ctx.driver:
        ctx.ensure_page()
        for i, b in items:
            els = find(ctx, b["locator"])
            if not els:
                continue
            e = els[0]
            if not e.is_displayed():
                continue
            visible += 1
            name = (e.text or e.get_attribute("aria-label") or e.get_attribute("value") or e.get_attribute("title") or "").strip()
            if not name:
                problems.append(f"Button #{i} has no accessible name")
            if not e.is_enabled() and not b["disabled"]:
                problems.append(f"Button '{name}' is unexpectedly disabled")
        if visible == 0:
            raise TestFailure("None of the detected buttons are visible and locatable on the page.", "Locate buttons")
        if problems:
            raise TestFailure("; ".join(problems), "Inspect button state")
        return f"{visible} visible button(s) are enabled and have accessible names (buttons are inspected, not clicked, to avoid side effects)."
    for i, b in items:
        if not (b["text"] or b["aria_label"]):
            problems.append(f"Button #{i} has no text or aria-label")
    if problems:
        raise TestFailure("; ".join(problems), "Inspect button markup")
    return f"{len(items)} button(s) have accessible text. [static HTML/HTTP check, not run in a browser]"


def input_fields(ctx, p):
    items = _pick(ctx.profile.inputs, p, cap=10)
    if not items:
        raise TestSkipped("No input fields were detected.")
    problems, tested = [], 0
    if ctx.driver:
        ctx.ensure_page()
        ctx.shot("before")
        for i, f in items:
            if f["tag"] == "select" or f["type"] in ("checkbox", "radio", "file", "range", "color", "date", "datetime-local", "time", "month", "week"):
                continue
            els = find(ctx, f["locator"])
            if not els or not els[0].is_displayed() or not els[0].is_enabled():
                continue
            e = els[0]
            sample = SAMPLE_VALUES.get(f["type"] if f["tag"] == "input" else f["tag"], "TestPilot sample")
            try:
                e.clear()
                e.send_keys(sample)
                got = e.get_attribute("value") or ""
                maxlen = e.get_attribute("maxlength")
                if got != sample and not (maxlen and got and sample.startswith(got)):
                    problems.append(f"Field '{f['name'] or f['id'] or f['type']}' did not accept input (got '{got[:20]}')")
                tested += 1
                e.clear()
            except Exception as ex:
                problems.append(f"Field '{f['name'] or f['id']}' could not be typed into ({type(ex).__name__})")
        ctx.shot("after")
        if tested == 0 and not problems:
            raise TestSkipped("No visible, editable text fields were found.")
        if problems:
            raise TestFailure("; ".join(problems), "Type into field")
        return f"{tested} field(s) accepted typed input and were cleared again (nothing submitted)."
    for i, f in items:
        if not (f["label"] or f["placeholder"]):
            problems.append(f"Field #{i} ({f['name'] or f['type']}) has no label or placeholder")
    if problems:
        raise TestFailure("; ".join(problems), "Inspect input markup")
    return f"{len(items)} input(s) have a label or placeholder. [static HTML/HTTP check, not run in a browser]"


def forms(ctx, p):
    items = _pick(ctx.profile.forms, p, cap=3)
    if not items:
        raise TestSkipped("No forms were detected.")
    problems = []
    for i, f in items:
        if f["field_count"] == 0:
            problems.append(f"Form #{i} has no input fields")
        if not f["has_submit"]:
            problems.append(f"Form #{i} has no submit button")
        if f["action"].lower().startswith("javascript:"):
            problems.append(f"Form #{i} uses a javascript: action")
        if f["method"] not in ("get", "post", "dialog"):
            problems.append(f"Form #{i} has invalid method '{f['method']}'")
        if ctx.driver:
            ctx.ensure_page()
            els = find(ctx, f["locator"])
            if not els:
                problems.append(f"Form #{i} could not be located in the browser")
            elif els[0].is_displayed():
                ctx.shot(f"form{i}")
    if problems:
        raise TestFailure("; ".join(problems), "Inspect form structure")
    return f"{len(items)} form(s) have fields, a submit control and a valid method (forms are inspected, not submitted)."


def search_box(ctx, p):
    forms_ = ctx.profile.search_forms
    if not forms_:
        raise TestSkipped("No search form was detected.")
    f = forms_[0]
    name = f["search_input_name"]
    if ctx.driver:
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        ctx.ensure_page()
        sel = ("css", f"form#{f['id']} [name=\"{name}\"]") if f["id"] and name else f["locator"]
        form_els = find(ctx, f["locator"])
        inputs = form_els[0].find_elements("css selector", f"[name=\"{name}\"]") if (form_els and name) else []
        if not inputs or not inputs[0].is_displayed():
            raise TestFailure("The search input could not be located or is not visible.", "Locate search input")
        box = inputs[0]
        ctx.shot("before")
        box.clear()
        box.send_keys("test")
        if (box.get_attribute("value") or "") != "test":
            raise TestFailure("The search box did not accept typed text.", "Type query")
        if f["method"] != "get":
            box.clear()
            return "Search box accepts text. Submission skipped because the form uses POST (not auto-submitted for safety)."
        before = ctx.driver.current_url
        box.send_keys(Keys.ENTER)
        try:
            WebDriverWait(ctx.driver, 8).until(lambda d: d.current_url != before)
        except Exception:
            raise TestFailure("Submitting the search did not navigate or change the URL within 8s.", "Submit search")
        ctx.assert_allowed(ctx.driver.current_url)
        ctx.shot("results")
        ctx.ensure_page()
        return f"Search for 'test' navigated to {before} -> results page."
    if f["method"] != "get" or not name:
        raise TestSkipped("Search form is not a simple GET form; needs the browser engine.")
    from urllib.parse import urlencode
    url = f["action"] + ("&" if "?" in f["action"] else "?") + urlencode({name: "test"})
    r = ctx.http("GET", url, stream_limit=262144)
    if r.status_code >= 400:
        raise TestFailure(f"Search request returned HTTP {r.status_code}.", "Submit search")
    return f"GET {url} returned HTTP {r.status_code}. [static HTML/HTTP check, not run in a browser]"


def console_errors(ctx, p):
    ctx.require_browser("Console error detection")
    try:
        ctx.driver.get_log("browser")  # drain
    except Exception:
        raise TestSkipped("This browser does not expose console logs (Chrome required).")
    ctx.navigate(ctx.base_url)
    errs = [e for e in ctx.driver.get_log("browser") if e.get("level") == "SEVERE" and "favicon" not in e.get("message", "")]
    if errs:
        raise TestFailure(f"{len(errs)} console error(s): " + " | ".join(e["message"][:140] for e in errs[:3]), "Read browser console")
    return "No severe console errors were logged on page load."


def images_loaded(ctx, p):
    if not ctx.profile.images:
        raise TestSkipped("No images were detected.")
    if ctx.driver:
        ctx.ensure_page()
        res = ctx.driver.execute_script(
            "return Array.from(document.images).filter(i=>i.src && i.loading!=='lazy').map(i=>({s:i.src,ok:i.complete&&i.naturalWidth>0}))")
        bad = [r["s"] for r in res if not r["ok"]]
        if bad:
            raise TestFailure(f"{len(bad)} of {len(res)} images failed to render: " + ", ".join(b[-60:] for b in bad[:3]), "Render images")
        return f"All {len(res)} eagerly-loaded images rendered."
    bad = []
    for im in ctx.profile.images[:15]:
        try:
            r = ctx.http("GET", im["src"], stream_limit=4096)
            if r.status_code >= 400:
                bad.append(f"{im['src'][-50:]} (HTTP {r.status_code})")
        except Exception as e:
            bad.append(f"{im['src'][-50:]} ({type(e).__name__})")
    if bad:
        raise TestFailure(f"{len(bad)} image(s) failed: " + ", ".join(bad[:4]), "Request images")
    return f"{min(15, len(ctx.profile.images))} image URL(s) responded OK. [static HTML/HTTP check, not run in a browser]"


def image_alt_text(ctx, p):
    imgs = ctx.profile.images
    if not imgs:
        raise TestSkipped("No images were detected.")
    missing = [i["src"][-50:] for i in imgs if not i["has_alt"]]
    if missing:
        raise TestFailure(f"{len(missing)} of {len(imgs)} images have no alt attribute: " + ", ".join(missing[:3]), "Inspect img alt attributes")
    return f"All {len(imgs)} images have an alt attribute."


DESTRUCTIVE = re.compile(r"delet|remov|destroy|erase|wipe|drop|clear|reset|pay|buy|purchas|checkout|order|subscri|log ?out|sign ?out|submit|send|post|"
                         r"publish|confirm|cancel|deactivat|transfer|donat|upgrade|install|download|accept|agree|follow|like|vote|save|apply|book|reserve", re.I)


def click_buttons(ctx, p):
    """Click standalone, non-destructive buttons in the real browser and verify the page survives. Buttons inside <form>s
    and anything whose label looks destructive (delete, pay, send, submit...) are never clicked."""
    ctx.require_browser("Clicking buttons")
    cands, skipped = [], 0
    for b in ctx.profile.buttons:
        name = b["text"] or b["aria_label"]
        if b["disabled"] or b["in_form"] or b["type"] in ("reset", "image") or not name or not b["locator"][0] or DESTRUCTIVE.search(name):
            skipped += 1
        else:
            cands.append(b)
    if not cands:
        raise TestSkipped("No safe standalone buttons to click: buttons inside forms, disabled ones and ones named like delete/pay/send/submit are never clicked "
                          f"({skipped} skipped for safety).")
    d, effects, problems = ctx.driver, [], []
    for b in cands[:5]:
        name = b["text"] or b["aria_label"]
        ctx.ensure_page()
        el = next((e for e in find(ctx, b["locator"]) if e.is_displayed() and e.is_enabled()), None)
        if el is None:
            continue
        handles, url0 = list(d.window_handles), d.current_url
        dom0 = d.execute_script("return document.getElementsByTagName('*').length*1000+(document.body?document.body.innerText.length:0)")
        try:
            d.get_log("browser")  # drain so only click-caused messages are inspected
        except Exception:
            pass
        try:
            d.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
            el.click()
        except Exception as e:
            problems.append(f"'{name}' could not be clicked ({type(e).__name__}); it may be covered by another element or not interactive")
            continue
        time.sleep(0.4)
        _settle(ctx)
        _close_extra_windows(ctx, handles)
        try:
            ctx.assert_allowed(d.current_url)
        except UnsafeTargetError:
            problems.append(f"Clicking '{name}' navigated to a blocked address")
            continue
        try:
            errs = [e for e in d.get_log("browser") if e.get("level") == "SEVERE" and "favicon" not in e.get("message", "")]
        except Exception:
            errs = []
        if errs:
            problems.append(f"Clicking '{name}' caused {len(errs)} console error(s): " + " | ".join(e["message"][:120] for e in errs[:2]))
        else:
            dom1 = d.execute_script("return document.getElementsByTagName('*').length*1000+(document.body?document.body.innerText.length:0)")
            effects.append(f"'{name}' ({'URL changed' if d.current_url != url0 else 'page content changed' if dom1 != dom0 else 'no visible change'})")
            ctx.shot(f"click_{len(effects)}")
    ctx.ensure_page()
    if problems:
        raise TestFailure("; ".join(problems), "Click button")
    if not effects:
        raise TestSkipped("None of the safe buttons was visible and enabled in the browser.")
    return f"Clicked {len(effects)} safe button(s) in the real browser without errors: " + ", ".join(effects) + f". {skipped} button(s) were not clicked for safety."


def viewport_meta(ctx, p):
    """STATIC HTML check (reads the page markup); it is not a rendering test and is labelled Static/HTTP."""
    _needs_browser(ctx)
    if not ctx.profile.has_viewport_meta:
        raise TestFailure("No <meta name='viewport'> tag: phones render the desktop layout zoomed out.", "Inspect <head>")
    return "The page markup contains a viewport meta tag. (Static HTML check: it does not prove the layout is responsive.)"
