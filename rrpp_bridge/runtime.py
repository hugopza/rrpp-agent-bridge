from __future__ import annotations

import sqlite3

from .audit import record, utc_now
from .config import VALID_MODES
from .db import transaction

MODE_KEY = "execution_mode"


def initialize_mode(conn: sqlite3.Connection, default_mode: str) -> None:
    if default_mode not in VALID_MODES:
        raise ValueError("Invalid default execution mode")
    conn.execute(
        "INSERT OR IGNORE INTO runtime_settings(key,value,updated_at,updated_by) VALUES(?,?,?,?)",
        (MODE_KEY, default_mode, utc_now(), "config.startup"),
    )


def get_mode(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM runtime_settings WHERE key=?", (MODE_KEY,)).fetchone()
    if row is None or row["value"] not in VALID_MODES:
        raise RuntimeError("A valid runtime execution mode is required")
    return str(row["value"])


def set_mode(conn: sqlite3.Connection, mode: str, actor: str) -> tuple[str, str]:
    if mode not in VALID_MODES:
        raise ValueError("Invalid execution mode")
    with transaction(conn, immediate=True):
        previous = get_mode(conn)
        timestamp = utc_now()
        conn.execute(
            "UPDATE runtime_settings SET value=?,updated_at=?,updated_by=? WHERE key=?",
            (mode, timestamp, actor, MODE_KEY),
        )
        record(conn, actor, "mode.changed", "runtime_setting", MODE_KEY, "updated",
               {"previous": previous, "current": mode})
    return previous, mode
