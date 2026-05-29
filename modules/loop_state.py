"""SQLite state for the per-form production loop.

Tracks status (PENDING / DONE / FAILED / DEAD), attempt counts, and a
per-run history. The schema mirrors maine-forms-loop's `forms.db` /
`runs` tables but drops the rubric/judge columns since probate forms
don't go through the visual-judging step.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import config

logger = logging.getLogger(__name__)

DB_PATH = config.INTERMEDIATE_DIR / "loop_state.db"
MAX_ATTEMPTS = 3


SCHEMA = """
CREATE TABLE IF NOT EXISTS forms (
    form_id        TEXT PRIMARY KEY,
    category       TEXT NOT NULL,
    pdf_path       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'PENDING',
    attempts       INTEGER NOT NULL DEFAULT 0,
    fields_written INTEGER,
    last_run_at    TEXT,
    last_error     TEXT
);

CREATE INDEX IF NOT EXISTS forms_status_idx ON forms(status);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    form_id        TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,
    fields_written INTEGER,
    error          TEXT,
    FOREIGN KEY(form_id) REFERENCES forms(form_id)
);

CREATE INDEX IF NOT EXISTS runs_form_idx ON runs(form_id, started_at);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def init_from_downloads() -> dict[str, int]:
    """Populate forms table from the downloaded-forms catalog. Idempotent —
    existing rows keep their status; only new forms are added as PENDING."""
    from download import list_downloaded_forms

    added = 0
    skipped = 0
    forms = list_downloaded_forms()
    with connect() as conn:
        for f in forms:
            cur = conn.execute(
                "INSERT OR IGNORE INTO forms(form_id, category, pdf_path) VALUES (?,?,?)",
                (f["form_id"], f["category"], f["path"]),
            )
            if cur.rowcount:
                added += 1
            else:
                skipped += 1
    return {"added": added, "skipped_existing": skipped, "total": len(forms)}


def status_counts() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM forms GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def pick_pending() -> Optional[sqlite3.Row]:
    """Pick one random PENDING form. Returns None if queue is empty."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM forms WHERE status = 'PENDING' ORDER BY RANDOM() LIMIT 1"
        ).fetchall()
        return rows[0] if rows else None


def list_pending() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM forms WHERE status = 'PENDING' ORDER BY form_id"
        ).fetchall()


def list_dead() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM forms WHERE status = 'DEAD' ORDER BY form_id"
        ).fetchall()


def begin_run(form_id: str) -> int:
    with connect() as conn:
        conn.execute(
            "UPDATE forms SET status='IN_PROGRESS', attempts=attempts+1, last_run_at=? WHERE form_id=?",
            (now(), form_id),
        )
        cur = conn.execute(
            "INSERT INTO runs(form_id, started_at, status) VALUES (?,?, 'IN_PROGRESS')",
            (form_id, now()),
        )
        return cur.lastrowid


def finish_ok(form_id: str, run_id: int, fields_written: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE forms SET status='DONE', fields_written=?, last_error=NULL WHERE form_id=?",
            (fields_written, form_id),
        )
        conn.execute(
            "UPDATE runs SET finished_at=?, status='DONE', fields_written=? WHERE id=?",
            (now(), fields_written, run_id),
        )


def finish_err(form_id: str, run_id: int, error: str) -> str:
    """Record a failure. Returns the resulting form status (FAILED or DEAD)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM forms WHERE form_id=?", (form_id,)
        ).fetchone()
        attempts = row["attempts"] if row else MAX_ATTEMPTS
        new_status = "DEAD" if attempts >= MAX_ATTEMPTS else "FAILED"
        conn.execute(
            "UPDATE forms SET status=?, last_error=? WHERE form_id=?",
            (new_status, error[:500], form_id),
        )
        conn.execute(
            "UPDATE runs SET finished_at=?, status=?, error=? WHERE id=?",
            (now(), new_status, error[:2000], run_id),
        )
        return new_status


def reset_failed_to_pending() -> int:
    """Move FAILED rows back to PENDING for a retry sweep. Leaves DEAD alone."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE forms SET status='PENDING' WHERE status='FAILED'"
        )
        return cur.rowcount


def reset_form(form_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE forms SET status='PENDING', attempts=0, last_error=NULL WHERE form_id=?",
            (form_id,),
        )
        return cur.rowcount > 0
