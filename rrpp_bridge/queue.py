from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from .audit import record, utc_now
from .db import transaction
from .models import NormalizedEvent


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


class JobQueue:
    """SQLite/WAL durable queue with bounded retry and per-conversation serialization."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def enqueue(self, event: NormalizedEvent) -> tuple[str, bool]:
        event_id, job_id, timestamp = _id("evt"), _id("job"), utc_now()
        try:
            with transaction(self.conn, immediate=True):
                self.conn.execute(
                    "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (event_id, event.channel, event.external_message_id, event.sender,
                     event.recipient, event.subject, event.body_text, event.received_at or timestamp, timestamp,
                     json.dumps(event.metadata, separators=(",", ":")), event.work_key, "queued"),
                )
                self.conn.execute(
                    "INSERT INTO jobs(id,event_id,work_key,state,available_at,created_at,updated_at) "
                    "VALUES(?,?,?,'queued',?,?,?)",
                    (job_id, event_id, event.work_key, timestamp, timestamp, timestamp),
                )
                record(self.conn, f"adapter.{event.channel}", "event.accepted", "event",
                       event_id, "accepted", {"channel": event.channel, "job_id": job_id})
            return event_id, True
        except sqlite3.IntegrityError:
            row = self.conn.execute(
                "SELECT id FROM events WHERE channel=? AND external_message_id=?",
                (event.channel, event.external_message_id),
            ).fetchone()
            if row is None:
                raise
            record(self.conn, f"adapter.{event.channel}", "event.duplicate", "event",
                   row["id"], "ignored")
            return str(row["id"]), False

    def claim_next(self, worker_id: str, lease_seconds: int = 60) -> sqlite3.Row | None:
        timestamp = utc_now()
        with transaction(self.conn, immediate=True):
            job = self.conn.execute(
                "SELECT * FROM jobs j WHERE j.state='queued' AND j.available_at<=? "
                "AND NOT EXISTS (SELECT 1 FROM jobs r WHERE r.work_key=j.work_key "
                "AND r.state='processing') ORDER BY j.created_at LIMIT 1", (timestamp,),
            ).fetchone()
            if job is None:
                return None
            self.conn.execute(
                "UPDATE jobs SET state='processing',attempts=attempts+1,claimed_at=?,"
                "lease_expires_at=?,worker_id=?,updated_at=? WHERE id=?",
                (timestamp, _after(lease_seconds), worker_id, timestamp, job["id"]),
            )
            record(self.conn, worker_id, "job.claimed", "job", job["id"], "processing",
                   {"attempt": int(job["attempts"]) + 1, "work_key": job["work_key"]})
        return self.conn.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

    def fail(self, job: sqlite3.Row, exc: Exception, worker_id: str, max_attempts: int) -> None:
        attempts = int(job["attempts"])
        terminal = attempts >= max_attempts
        state, timestamp = ("dead_letter" if terminal else "queued"), utc_now()
        delay = min(5 * (2 ** max(0, attempts - 1)), 300)
        available_at = timestamp if terminal else _after(delay)
        code, message = type(exc).__name__, "Job processing failed; inspect correlated diagnostics"
        with transaction(self.conn, immediate=True):
            self.conn.execute(
                "UPDATE jobs SET state=?,available_at=?,last_error_code=?,last_error_message=?,"
                "lease_expires_at=NULL,worker_id=NULL,updated_at=? WHERE id=?",
                (state, available_at, code, message, timestamp, job["id"]),
            )
            self.conn.execute("UPDATE events SET status=? WHERE id=?",
                              ("failed" if terminal else "retrying", job["event_id"]))
            record(self.conn, worker_id, "job.failed", "job", job["id"], state,
                   {"error_code": code, "attempt": attempts, "retry_delay_seconds": 0 if terminal else delay})

    def recover_stale(self, max_attempts: int, actor: str = "worker.recovery") -> int:
        timestamp = utc_now()
        with transaction(self.conn, immediate=True):
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE state='processing' AND lease_expires_at IS NOT NULL "
                "AND lease_expires_at<=?", (timestamp,),
            ).fetchall()
            for job in rows:
                terminal = int(job["attempts"]) >= max_attempts
                state = "dead_letter" if terminal else "queued"
                self.conn.execute(
                    "UPDATE jobs SET state=?,available_at=?,lease_expires_at=NULL,worker_id=NULL,"
                    "last_error_code='lease_expired',last_error_message='Worker lease expired',updated_at=? "
                    "WHERE id=?", (state, timestamp, timestamp, job["id"]),
                )
                self.conn.execute("UPDATE events SET status=? WHERE id=?",
                                  ("failed" if terminal else "retrying", job["event_id"]))
                record(self.conn, actor, "job.lease_recovered", "job", job["id"], state,
                       {"attempt": job["attempts"]})
        return len(rows)

    def retry(self, job_id: str, actor: str) -> bool:
        timestamp = utc_now()
        with transaction(self.conn, immediate=True):
            job = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None or job["state"] != "dead_letter":
                return False
            self.conn.execute(
                "UPDATE jobs SET state='queued',attempts=0,available_at=?,claimed_at=NULL,"
                "lease_expires_at=NULL,worker_id=NULL,last_error_code=NULL,last_error_message=NULL,"
                "updated_at=? WHERE id=?", (timestamp, timestamp, job_id),
            )
            self.conn.execute("UPDATE events SET status='queued' WHERE id=?", (job["event_id"],))
            record(self.conn, actor, "job.retried", "job", job_id, "queued",
                   {"previous_attempts": job["attempts"]})
        return True

    def dismiss(self, job_id: str, actor: str) -> bool:
        timestamp = utc_now()
        with transaction(self.conn, immediate=True):
            job = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None or job["state"] != "dead_letter":
                return False
            self.conn.execute(
                "UPDATE jobs SET state='dismissed',dismissed_at=?,updated_at=? WHERE id=?",
                (timestamp, timestamp, job_id),
            )
            self.conn.execute("UPDATE events SET status='dismissed' WHERE id=?", (job["event_id"],))
            record(self.conn, actor, "job.dismissed", "job", job_id, "dismissed")
        return True
