"""Compatibility facade for the core bridge application services."""

from __future__ import annotations

import sqlite3
from typing import Any

from .adapters.local import normalize
from .agent_provider import AgentProvider
from .executor import Executor
from .queue import JobQueue


def ingest_local(conn: sqlite3.Connection, payload: dict[str, Any]) -> tuple[str, bool]:
    return JobQueue(conn).enqueue(normalize(payload))


def process_one(conn: sqlite3.Connection, worker_id: str = "worker.local",
                max_attempts: int = 3, lease_seconds: int = 60,
                canary_senders: frozenset[str] = frozenset(),
                agent_provider: AgentProvider | None = None) -> bool:
    return Executor(conn, max_attempts, lease_seconds, canary_senders,
                    agent_provider).run_once(worker_id)
