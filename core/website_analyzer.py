"""Analyze a publicly reachable page and build a SiteProfile (title, nav, buttons, inputs, forms, ...)."""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from core.models import SiteProfile
from core.safety import safe_request
from utils.helpers import absolute, clean_text, css_escape, same_site, xpath_literal

TEXT_INPUT_TYPES = {"text", "email", "search", "tel", "url", "password", "number", ""}
MAX_ITEMS = 200


def _label_for(soup, el) -> str:
    if el.get("aria-label"):
        return clean_text(el["aria-label"], 60)
    if el.get("id"):
        lab = soup.find("label", attrs={"for": el["id"]})
        if lab:
            return clean_text(lab.get_text(), 60)
    parent = el.find_parent("label")
    return clean_text(parent.get_text(), 60) if parent else ""


def _locator(el, tag: str, text: str = "") -> tuple[str, str]:
    if el.get("id"):
        return ("css", f"#{css_escape(el['id'])}")
    if el.get("name") and tag in ("input", "select", "textarea", "button"):
        return ("css", f"{tag}[name=\"{el['name']}\"]")
    if text and tag in ("a", "button"):
        return ("xpath", f"//{tag}[normalize-space(.)={xpath_literal(text)}]")
    if el.get("placeholder"):
        return ("css", f"{tag}[placeholder=\"{el['placeholder']}\"]")
    if tag == "input" and el.get("type") == "submit" and el.get("value"):
        return ("css", f"input[type=\"submit\"][value=\"{el['value']}\"]")
    return ("", "")


PAGE_NOTE_PATTERNS = [
    (r"gone to sleep|app is (?:currently )?(?:asleep|sleeping|hibernating)|get this app back up|wake (?:this )?app up",
     "The page says the app is asleep/hibernating. Open it once in a browser to wake it, then re-run the test."),
    (r"just a moment\.\.\.|checking your browser|attention required|verify you are (?:a )?human|captcha",
     "The page looks like bot protection or a CAPTCHA challenge, so the results describe the challenge page, not the site."),
    (r"under maintenance|be right back|temporarily unavailable",
     "The page says the site is under maintenance or temporarily unavailable."),
]


def _detect_notes(soup, profile: SiteProfile) -> None:
    """Heuristic, informational only: flags pages whose static HTML is not the real site."""
    clone = BeautifulSoup(str(soup), "lxml")
    for t in clone(["script", "style", "noscript", "template"]):
        t.decompose()
    text = re.sub(r"\s+", " ", clone.get_text(" ")).strip()
    interactive = len(profile.links) + len(profile.buttons) + len(profile.inputs) + len(profile.forms)
    has_scripts = bool(soup.find("script"))
    root = soup.find(id=re.compile(r"^(root|app|__next|__nuxt|react-root|svelte)$", re.I))
    if len(text) < 200 and interactive <= 2 and (has_scripts or root):
        profile.js_rendered_shell = True
        profile.notes.append("The page is rendered by JavaScript (e.g. Streamlit, React, Vue): its HTML is almost empty until a browser runs it. "
                             + ("The browser engine waited for it to render but little content appeared." if profile.rendered_with_browser
                                else "The static HTTP engine can only see the empty shell; use the Selenium (Chrome) engine for meaningful results."))
    low = text.lower()[:20000]
    for pat, msg in PAGE_NOTE_PATTERNS:
        if re.search(pat, low):
            profile.notes.append(msg)


def parse_html(html: str, base_url: str, profile: SiteProfile) -> SiteProfile:
    soup = BeautifulSoup(html, "lxml")
    profile.html_bytes = len(html.encode("utf-8", "ignore"))
    profile.title = clean_text(soup.title.get_text() if soup.title else "", 200)
    if soup.html and soup.html.get("lang"):
        profile.lang = soup.html["lang"]
    md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    profile.meta_description = clean_text(md.get("content", "") if md else "", 300)
    profile.has_viewport_meta = bool(soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}))

    profile.headings = [{"level": h.name, "text": clean_text(h.get_text(), 100)}
                        for h in soup.find_all(re.compile("^h[1-6]$"))[:MAX_ITEMS] if h.get_text(strip=True)]

    seen = set()
    for a in soup.find_all("a", href=True)[:1000]:
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
            continue
        full = absolute(base_url, href).split("#")[0]
        text = clean_text(a.get_text() or a.get("aria-label") or a.get("title") or "", 80)
        in_nav = bool(a.find_parent(["nav", "header"]))
        key = (full, text)
        if key in seen:
            continue
        seen.add(key)
        item = {"text": text, "href": full, "internal": same_site(full, base_url), "in_nav": in_nav,
                "locator": _locator(a, "a", text)}
        profile.links.append(item)
        if in_nav:
            profile.nav_links.append(item)
    profile.links, profile.nav_links = profile.links[:MAX_ITEMS], profile.nav_links[:MAX_ITEMS]

    for b in soup.find_all(["button", "input"]):
        if b.name == "input" and b.get("type") not in ("button", "submit", "reset", "image"):
            continue
        text = clean_text(b.get_text() if b.name == "button" else b.get("value", ""), 60)
        profile.buttons.append({"text": text, "type": b.get("type", "submit" if b.name == "button" else "button"),
                                "aria_label": b.get("aria-label", ""), "id": b.get("id", ""),
                                "disabled": b.has_attr("disabled"), "in_form": bool(b.find_parent("form")),
                                "locator": _locator(b, b.name, text)})
    profile.buttons = profile.buttons[:MAX_ITEMS]

    for i in soup.find_all(["input", "textarea", "select"]):
        t = (i.get("type") or "text").lower() if i.name == "input" else i.name
        if t in ("hidden", "submit", "button", "reset", "image"):
            continue
        profile.inputs.append({"tag": i.name, "type": t, "name": i.get("name", ""), "id": i.get("id", ""),
                               "placeholder": i.get("placeholder", ""), "label": _label_for(soup, i),
                               "required": i.has_attr("required"), "locator": _locator(i, i.name)})
    profile.inputs = profile.inputs[:MAX_ITEMS]

    for idx, f in enumerate(soup.find_all("form")[:50]):
        fields = [x for x in f.find_all(["input", "textarea", "select"])
                  if (x.get("type") or "text").lower() not in ("hidden", "submit", "button", "reset", "image")]
        has_submit = bool(f.find(["button"], attrs={"type": lambda v: v in (None, "submit")}) or
                          f.find("input", attrs={"type": "submit"}))
        method = (f.get("method") or "get").lower()
        is_search = bool(f.get("role") == "search" or f.find("input", attrs={"type": "search"}) or
                         re.search(r"search", (f.get("id", "") + " ".join(f.get("class", [])) + f.get("action", "")), re.I) or
                         any(re.search(r"^(q|s|search|query|keyword)$", x.get("name", ""), re.I) for x in fields))
        first_text = next((x for x in fields if (x.get("type") or "text").lower() in TEXT_INPUT_TYPES and x.name != "select"), None)
        profile.forms.append({
            "index": idx, "id": f.get("id", ""), "action": absolute(base_url, f.get("action", "")) if f.get("action") else base_url,
            "method": method, "field_count": len(fields), "has_submit": has_submit, "is_search": is_search,
            "search_input_name": first_text.get("name", "") if (first_text is not None and is_search) else "",
            "field_names": [x.get("name") or x.get("id") or x.get("type", "") for x in fields][:15],
            "locator": ("css", f"form#{css_escape(f['id'])}") if f.get("id") else ("xpath", f"(//form)[{idx + 1}]")})

    for im in soup.find_all("img")[:MAX_ITEMS]:
        src = im.get("src") or im.get("data-src") or ""
        if src and not src.startswith("data:"):
            profile.images.append({"src": absolute(base_url, src), "alt": im.get("alt"), "has_alt": im.has_attr("alt")})

    feats = []
    if profile.nav_links: feats.append("Site navigation")
    if profile.search_forms or soup.find("input", attrs={"type": "search"}): feats.append("Search")
    if any(i["type"] == "password" for i in profile.inputs): feats.append("Login / authentication")
    if any(i["type"] == "email" for i in profile.inputs) and any(f["field_count"] <= 3 for f in profile.forms): feats.append("Email capture / contact")
    if profile.forms: feats.append("Forms")
    if profile.images: feats.append("Images / media")
    if soup.find(["video", "audio", "iframe"]): feats.append("Embedded media")
    if re.search(r"cart|checkout|add to (cart|bag)", soup.get_text(" ").lower()[:50000]): feats.append("E-commerce signals")
    if soup.find("footer"): feats.append("Footer")
    profile.features = feats
    _detect_notes(soup, profile)
    return profile


def analyze_website(url: str, *, allow_loopback=False, allow_private=False, timeout=10, html: str | None = None,
                    final_url: str | None = None) -> SiteProfile:
    """Fetch (unless `html` is supplied, e.g. from Selenium) and parse the page."""
    profile = SiteProfile(url=url)
    if html is None:
        t0 = time.perf_counter()
        r = safe_request("GET", url, allow_loopback=allow_loopback, allow_private=allow_private, timeout=timeout)
        profile.load_time = time.perf_counter() - t0
        profile.status_code, profile.final_url = r.status_code, r.url
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype.lower() and "xml" not in ctype.lower() and r.content[:1] != b"<":
            raise ValueError(f"The URL did not return an HTML page (Content-Type: {ctype or 'unknown'}).")
        r.encoding = r.encoding or r.apparent_encoding
        html = r.text
    else:
        profile.final_url = final_url or url
        profile.rendered_with_browser = True
    return parse_html(html, profile.final_url or url, profile)
