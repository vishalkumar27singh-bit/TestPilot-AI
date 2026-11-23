"""Whitelist of executable actions. Nothing outside this table can ever be executed,
which is how AI-generated test plans are kept safe (they can only *reference* these actions)."""
from testing import link_tests, responsive_tests, ui_tests

ACTIONS = {
    "page_load": ui_tests.page_load,
    "page_title": ui_tests.page_title,
    "http_availability": ui_tests.http_availability,
    "navigation_links": ui_tests.navigation_links,
    "broken_links": link_tests.broken_links,
    "check_link": link_tests.check_link,
    "buttons": ui_tests.buttons,
    "input_fields": ui_tests.input_fields,
    "forms": ui_tests.forms,
    "search_box": ui_tests.search_box,
    "responsive": responsive_tests.responsive,
    "console_errors": ui_tests.console_errors,
    "click_buttons": ui_tests.click_buttons,
    "viewport_meta": ui_tests.viewport_meta,
    "images_loaded": ui_tests.images_loaded,
    "image_alt_text": ui_tests.image_alt_text,
}
# action -> allowed params with validators
PARAM_RULES = {
    "index": lambda v: isinstance(v, int) and 0 <= v < 500,
    "viewport": lambda v: v in ("desktop", "tablet", "mobile"),
    "max_links": lambda v: isinstance(v, int) and 1 <= v <= 50,
}
ACTION_PARAMS = {"buttons": {"index"}, "input_fields": {"index"}, "forms": {"index"}, "check_link": {"index"},
                 "responsive": {"viewport"}, "broken_links": {"max_links"}}

# How each action runs. "browser" = only meaningful in a real browser (SKIPPED without one); "prefer" = runs in the browser when
# there is one, otherwise a static/HTTP variant that is LABELLED static; "static" = always HTML/HTTP analysis.
ACTION_MODE = {"responsive": "browser", "console_errors": "browser", "click_buttons": "browser",
               "page_load": "prefer", "page_title": "prefer", "navigation_links": "prefer", "buttons": "prefer", "input_fields": "prefer",
               "forms": "prefer", "search_box": "prefer", "images_loaded": "prefer",
               "http_availability": "static", "broken_links": "static", "check_link": "static", "image_alt_text": "static", "viewport_meta": "static"}


def engine_label(action: str, status: str, browser_running: bool) -> str:
    """Honest label for how a result was produced. Never 'Browser' unless a real browser executed the check."""
    if status == "SKIPPED":
        return "Not run"
    mode = ACTION_MODE.get(action, "static")
    return "Browser" if (browser_running and mode in ("browser", "prefer")) else "Static/HTTP"
