PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'ui',          -- ui | api
    url TEXT NOT NULL,
    final_url TEXT, engine TEXT, ai_mode TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING',    -- RUNNING | COMPLETED | ERROR
    started_at TEXT NOT NULL, finished_at TEXT,
    duration REAL DEFAULT 0,
    total INTEGER DEFAULT 0, passed INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0,
    config_json TEXT, profile_json TEXT, log_text TEXT, ai_summary TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS test_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    test_id TEXT, name TEXT, category TEXT, objective TEXT, steps_json TEXT, expected TEXT, actual TEXT,
    status TEXT, severity TEXT, error TEXT, failed_step TEXT, probable_cause TEXT, suggested_fix TEXT,
    ai_explanation TEXT, screenshot TEXT, duration REAL, source TEXT, action TEXT, started_at TEXT, engine TEXT
);
CREATE TABLE IF NOT EXISTS bugs (
    bug_id TEXT PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    test_id TEXT, title TEXT, description TEXT, severity TEXT, steps_to_reproduce TEXT,
    expected TEXT, actual TEXT, screenshot TEXT, created_at TEXT, suggested_solution TEXT, status TEXT DEFAULT 'OPEN'
);
CREATE TABLE IF NOT EXISTS screenshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    test_id TEXT, label TEXT, path TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tc_run ON test_cases(run_id);
CREATE INDEX IF NOT EXISTS idx_bug_run ON bugs(run_id);
