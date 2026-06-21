from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Iterator

MIGRATION_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _migrations() -> list[tuple[int, str]]:
    root = resources.files("rrpp_bridge.sql")
    found: list[tuple[int, str]] = []
    for item in root.iterdir():
        match = MIGRATION_RE.match(item.name)
        if match:
            found.append((int(match.group(1)), item.read_text(encoding="utf-8")))
    return sorted(found)


def _statements(script: str) -> Iterator[str]:
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                yield statement
            pending = ""
    if pending.strip():
        raise ValueError("Incomplete SQL migration statement")


def current_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0])


def latest_version() -> int:
    migrations = _migrations()
    return migrations[-1][0] if migrations else 0


def initialize(conn: sqlite3.Connection) -> list[int]:
    applied: list[int] = []
    for version, sql in _migrations():
        with transaction(conn, immediate=True):
            if version <= current_version(conn):
                continue
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES(?,datetime('now'))",
                (version,),
            )
            applied.append(version)
    return applied


def prepare_runtime(conn: sqlite3.Connection) -> list[int]:
    version = current_version(conn)
    if version == 0:
        return initialize(conn)
    target = latest_version()
    if version < target:
        raise RuntimeError(
            f"Database schema {version} is outdated; run 'rrpp-bridge migrate' before startup"
        )
    return []


def backup_database(path: Path | str) -> Path | None:
    source = Path(path)
    if not source.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = source.with_name(f"{source.name}.pre-migration-{stamp}.bak")
    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(target)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    return target


@contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = False) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
