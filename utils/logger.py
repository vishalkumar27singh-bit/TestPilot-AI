"""Run logger: collects timestamped entries and forwards them to an optional UI callback."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("testpilot")

ICONS = {"info": "•", "ok": "✔", "fail": "✘", "warn": "!", "step": "▶"}


class RunLog:
    def __init__(self, callback: Callable[[str, list[str]], None] | None = None):
        self.entries: list[str] = []
        self.callback = callback

    def add(self, message: str, level: str = "info") -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {ICONS.get(level, '•')} {message}"
        self.entries.append(line)
        log.info(message)
        if self.callback:
            try:
                self.callback(line, self.entries)
            except Exception:  # UI problems must never break a test run
                pass

    def text(self) -> str:
        return "\n".join(self.entries)
