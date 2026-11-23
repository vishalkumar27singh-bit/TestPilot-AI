"""Shared data models."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
STATUSES = ["PASS", "FAIL", "SKIPPED"]


class TestFailure(AssertionError):
    """Raised by a test handler when an assertion fails. `step` names the failing step."""

    __test__ = False

    def __init__(self, message: str, step: str = ""):
        super().__init__(message)
        self.step = step


class TestSkipped(Exception):
    __test__ = False


@dataclass
class TestCase:
    __test__ = False  # stop pytest trying to collect this class

    test_id: str
    name: str
    category: str
    objective: str
    steps: list[str]
    expected: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    severity: str = "MEDIUM"
    source: str = "rule"  # rule | ai
    actual: str = ""
    status: str = "PENDING"
    error: str = ""
    failed_step: str = ""
    screenshot: str = ""
    duration: float = 0.0
    probable_cause: str = ""
    suggested_fix: str = ""
    ai_explanation: str = ""
    engine: str = ""  # "Browser" (really executed in Chrome) | "Static/HTTP" | "Not run"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SiteProfile:
    url: str
    final_url: str = ""
    status_code: int = 0
    load_time: float = 0.0
    title: str = ""
    lang: str = ""
    meta_description: str = ""
    has_viewport_meta: bool = False
    headings: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    nav_links: list[dict] = field(default_factory=list)
    buttons: list[dict] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)
    forms: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    html_bytes: int = 0
    rendered_with_browser: bool = False
    js_rendered_shell: bool = False
    redirect_unresolved: bool = False  # server answered but an HTTP client could not follow its redirects to a page
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def search_forms(self) -> list[dict]:
        return [f for f in self.forms if f.get("is_search")]
