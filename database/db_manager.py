from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config.settings import DB_PATH, SCREENSHOT_DIR
from core.models import TestCase
from utils.helpers import now_iso

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class DB:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            # migration for databases created by older versions (no per-test engine column)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(test_cases)").fetchall()}
            if "engine" not in cols:
                c.execute("ALTER TABLE test_cases ADD COLUMN engine TEXT")

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _all(self, sql, args=()):
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    # ---- runs
    def create_run(self, url: str, kind="ui", engine="", ai_mode="", config: dict | None = None) -> int:
        with self.conn() as c:
            cur = c.execute("INSERT INTO runs(kind,url,engine,ai_mode,started_at,config_json) VALUES(?,?,?,?,?,?)",
                            (kind, url, engine, ai_mode, now_iso(), json.dumps(config or {}, default=str)))
            return cur.lastrowid

    def finish_run(self, run_id: int, status="COMPLETED", duration=0.0, tests: list[TestCase] | None = None, **extra):
        tests = tests or []
        counts = (len(tests), sum(t.status == "PASS" for t in tests), sum(t.status == "FAIL" for t in tests), sum(t.status == "SKIPPED" for t in tests))
        cols = {"final_url": None, "profile_json": None, "log_text": None, "ai_summary": None, "error": None, "engine": None}
        vals = {k: extra.get(k) for k in cols if extra.get(k) is not None}
        sets = ", ".join(f"{k}=?" for k in vals)
        with self.conn() as c:
            c.execute(f"UPDATE runs SET status=?, finished_at=?, duration=?, total=?, passed=?, failed=?, skipped=?{', ' + sets if sets else ''} WHERE id=?",
                      (status, now_iso(), duration, *counts, *vals.values(), run_id))

    def get_run(self, run_id: int) -> dict | None:
        r = self._all("SELECT * FROM runs WHERE id=?", (run_id,))
        return r[0] if r else None

    def list_runs(self, kind: str | None = None, limit: int = 500) -> list[dict]:
        q, a = "SELECT * FROM runs", []
        if kind:
            q += " WHERE kind=?"; a.append(kind)
        return self._all(q + " ORDER BY id DESC LIMIT ?", (*a, limit))

    def delete_run(self, run_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM runs WHERE id=?", (run_id,))
        shutil.rmtree(SCREENSHOT_DIR / f"run_{run_id}", ignore_errors=True)

    # ---- tests
    def add_test_cases(self, run_id: int, tests: list[TestCase]):
        with self.conn() as c:
            c.executemany(
                "INSERT INTO test_cases(run_id,test_id,name,category,objective,steps_json,expected,actual,status,severity,error,failed_step,"
                "probable_cause,suggested_fix,ai_explanation,screenshot,duration,source,action,started_at,engine) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, t.test_id, t.name, t.category, t.objective, json.dumps(t.steps), t.expected, t.actual, t.status, t.severity, t.error,
                  t.failed_step, t.probable_cause, t.suggested_fix, t.ai_explanation, t.screenshot, t.duration, t.source, t.action, now_iso(), t.engine or "") for t in tests])

    def get_test_cases(self, run_id: int) -> list[dict]:
        rows = self._all("SELECT * FROM test_cases WHERE run_id=? ORDER BY id", (run_id,))
        for r in rows:
            r["steps"] = json.loads(r.pop("steps_json") or "[]")
        return rows

    def all_test_cases(self) -> list[dict]:
        return self._all("SELECT t.*, r.url, r.kind, r.started_at AS run_started FROM test_cases t JOIN runs r ON r.id=t.run_id")

    # ---- bugs / screenshots
    def add_bugs(self, bugs: list[dict]):
        with self.conn() as c:
            c.executemany("INSERT OR REPLACE INTO bugs(bug_id,run_id,test_id,title,description,severity,steps_to_reproduce,expected,actual,screenshot,created_at,suggested_solution,status) "
                          "VALUES(:bug_id,:run_id,:test_id,:title,:description,:severity,:steps_to_reproduce,:expected,:actual,:screenshot,:created_at,:suggested_solution,:status)", bugs)

    def list_bugs(self, run_id: int | None = None) -> list[dict]:
        if run_id:
            return self._all("SELECT * FROM bugs WHERE run_id=? ORDER BY bug_id", (run_id,))
        return self._all("SELECT * FROM bugs ORDER BY created_at DESC, bug_id")

    def set_bug_status(self, bug_id: str, status: str):
        with self.conn() as c:
            c.execute("UPDATE bugs SET status=? WHERE bug_id=?", (status, bug_id))

    def add_screenshots(self, run_id: int, shots: list[tuple[str, str, str]]):
        with self.conn() as c:
            c.executemany("INSERT INTO screenshots(run_id,test_id,label,path,created_at) VALUES(?,?,?,?,?)",
                          [(run_id, tid, lab, p, now_iso()) for tid, lab, p in shots])

    def list_screenshots(self, run_id: int | None = None) -> list[dict]:
        if run_id:
            return self._all("SELECT * FROM screenshots WHERE run_id=? ORDER BY id", (run_id,))
        return self._all("SELECT * FROM screenshots ORDER BY id DESC")

    # ---- analytics
    def stats(self) -> dict:
        r = self._all("SELECT COUNT(*) runs, COALESCE(SUM(total),0) total, COALESCE(SUM(passed),0) passed, COALESCE(SUM(failed),0) failed, "
                      "COALESCE(SUM(skipped),0) skipped, COALESCE(AVG(NULLIF(duration,0)),0) avg_duration FROM runs WHERE status!='RUNNING'")[0]
        executed = r["passed"] + r["failed"]
        r["pass_rate"] = round(100 * r["passed"] / executed, 1) if executed else 0.0
        return r

    def compare_runs(self, a: int, b: int) -> list[dict]:
        """Per-test comparison by name (test ids can differ between runs when AI tests are added)."""
        ta = {t["name"]: t for t in self.get_test_cases(a)}
        tb = {t["name"]: t for t in self.get_test_cases(b)}
        out = []
        for name in list(dict.fromkeys([*ta, *tb])):
            sa, sb = (ta.get(name) or {}).get("status", "—"), (tb.get(name) or {}).get("status", "—")
            change = "same" if sa == sb else ("regression" if sa == "PASS" and sb == "FAIL" else "fixed" if sa == "FAIL" and sb == "PASS" else "changed")
            out.append({"test": name, f"run_{a}": sa, f"run_{b}": sb, "change": change})
        return out
