"""Compatibility facade for the core bridge application services."""

from __future__ import annotations

import sqlite3
from typing import Any

from .adapters.local import normalize
from .agent_provider import AgentProvider
from .executor import Executor
from .queue import JobQueue


def ingest_local(conn: sqlite3.Connection, payload: dict[str, Any],
                 debounce_seconds: int = 0) -> tuple[str, bool]:
    return JobQueue(conn, debounce_seconds).enqueue(normalize(payload))


def process_one(conn: sqlite3.Connection, worker_id: str = "worker.local",
                max_attempts: int = 3, lease_seconds: int = 60,
                canary_senders: frozenset[str] = frozenset(),
                agent_provider: AgentProvider | None = None, instagram_senders=None,
                *, instagram_send_enabled: bool | None = None,
                instagram_account_tokens=None) -> bool:
    return Executor(conn, max_attempts, lease_seconds, canary_senders,
                    agent_provider, instagram_senders,
                    instagram_send_enabled=instagram_send_enabled,
                    instagram_account_tokens=instagram_account_tokens).run_once(worker_id)
