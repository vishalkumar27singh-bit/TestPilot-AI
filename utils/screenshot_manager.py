from __future__ import annotations

from pathlib import Path

from config.settings import SCREENSHOT_DIR
from utils.helpers import slugify


class ScreenshotManager:
    """Saves screenshots under screenshots/run_<id>/ and returns paths relative to the project."""

    def __init__(self, run_id: int):
        self.dir = SCREENSHOT_DIR / f"run_{run_id}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def capture(self, driver, test_id: str, tag: str) -> str:
        path = self.dir / f"{slugify(test_id)}_{slugify(tag)}.png"
        try:
            if driver.save_screenshot(str(path)) and path.exists() and path.stat().st_size > 0:
                return str(path)
        except Exception:
            pass
        return ""


def read_bytes(path: str) -> bytes | None:
    try:
        p = Path(path)
        return p.read_bytes() if p.exists() else None
    except Exception:
        return None
