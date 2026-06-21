from __future__ import annotations

import json
import sqlite3
import uuid

from .action_executor import LocalActionExecutor
from .agent import generate_action
from .audit import record, utc_now
from .db import transaction
from .policy import Policy
from .queue import JobQueue
from .runtime import get_mode
from .workspace import create_review


class Executor:
    def __init__(self, conn: sqlite3.Connection, max_attempts: int = 3,
                 lease_seconds: int = 60, canary_senders: frozenset[str] = frozenset()):
        self.conn = conn
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.queue = JobQueue(conn)
        self.policy = Policy()
        self.action_executor = LocalActionExecutor(conn, canary_senders)

    def _action_for_job(self, job: sqlite3.Row, event: sqlite3.Row,
                        worker_id: str) -> tuple[sqlite3.Row, str]:
        existing = self.conn.execute(
            "SELECT a.*,p.outcome FROM actions a JOIN policy_decisions p ON p.action_id=a.id "
            "WHERE a.job_id=? ORDER BY a.created_at LIMIT 1", (job["id"],),
        ).fetchone()
        if existing:
            return existing, str(existing["outcome"])
        language_row = self.conn.execute(
            "SELECT COALESCE(v.default_language,'ca') language FROM events e "
            "LEFT JOIN conversations c ON c.id=e.conversation_id "
            "LEFT JOIN venues v ON v.id=c.venue_id WHERE e.id=?",
            (event["id"],),
        ).fetchone()
        intended = generate_action(event["body_text"], str(language_row["language"]) if language_row else "ca")
        decision = self.policy.decide(intended)
        action_id, decision_id, timestamp = (
            f"act_{uuid.uuid4().hex}", f"dec_{uuid.uuid4().hex}", utc_now()
        )
        with transaction(self.conn, immediate=True):
            self.conn.execute(
                "INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)",
                (action_id, event["id"], job["id"], intended.type,
                 json.dumps(intended.payload, separators=(",", ":")), "pending_review", get_mode(self.conn),
                 timestamp, timestamp),
            )
            self.conn.execute("INSERT INTO policy_decisions VALUES(?,?,?,?,?,?)", (
                decision_id, action_id, decision.outcome, decision.policy_id,
                decision.reason, timestamp,
            ))
            record(self.conn, worker_id, "action.generated", "action", action_id, "decided",
                   {"type": intended.type})
            record(self.conn, "policy", "action.decided", "action", action_id,
                   decision.outcome, {"policy_id": decision.policy_id})
            kind = "draft" if intended.type == "draft_reply" else "escalation"
            review_text = str(intended.payload.get("text", "")) if kind == "draft" else ""
            create_review(self.conn, action_id, kind, review_text, worker_id)
            self.conn.execute(
                "UPDATE conversations SET status='pending_review',updated_at=? "
                "WHERE id=?", (timestamp, event["conversation_id"]),
            )
        row = self.conn.execute(
            "SELECT a.*,p.outcome FROM actions a JOIN policy_decisions p ON p.action_id=a.id "
            "WHERE a.id=?", (action_id,),
        ).fetchone()
        return row, decision.outcome

    def run_once(self, worker_id: str = "worker.local") -> bool:
        self.queue.recover_stale(self.max_attempts)
        job = self.queue.claim_next(worker_id, self.lease_seconds)
        if job is None:
            return False
        try:
            event = self.conn.execute("SELECT * FROM events WHERE id=?", (job["event_id"],)).fetchone()
            if event is None:
                raise RuntimeError("event_not_found")
            action, outcome = self._action_for_job(job, event, worker_id)
            mode = get_mode(self.conn)  # Evaluate immediately before the execution boundary.
            result = self.action_executor.execute(action, outcome, event["sender"], mode, worker_id)
            timestamp = utc_now()
            with transaction(self.conn, immediate=True):
                self.conn.execute(
                    "UPDATE jobs SET state='completed',lease_expires_at=NULL,worker_id=NULL,updated_at=? "
                    "WHERE id=?", (timestamp, job["id"]),
                )
                self.conn.execute("UPDATE events SET status='processed' WHERE id=?", (event["id"],))
                record(self.conn, worker_id, "job.completed", "job", job["id"], "completed",
                       {"execution_status": result.status})
            return True
        except Exception as exc:
            self.queue.fail(job, exc, worker_id, self.max_attempts)
            return True
