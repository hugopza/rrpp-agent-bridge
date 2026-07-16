from __future__ import annotations

import json
import sqlite3
import uuid

from .action_executor import LocalActionExecutor
from .agent_provider import (AgentContext, AgentProvider, AgentProviderError, ConversationTurn,
                             DeterministicAgentProvider, detect_language_hint)
from .audit import record, utc_now
from .db import transaction
from .models import IntendedAction
from .policy import Policy
from .queue import JobQueue
from .runtime import get_mode
from .workspace import create_review


class Executor:
    def __init__(self, conn: sqlite3.Connection, max_attempts: int = 3,
                 lease_seconds: int = 60, canary_senders: frozenset[str] = frozenset(),
                 agent_provider: AgentProvider | None = None):
        self.conn = conn
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.queue = JobQueue(conn)
        self.policy = Policy()
        self.action_executor = LocalActionExecutor(conn, canary_senders)
        self.agent_provider = agent_provider or DeterministicAgentProvider()

    @staticmethod
    def _bounded(value: object, limit: int) -> str:
        return str(value or "")[:limit]

    def _agent_context(self, event: sqlite3.Row) -> AgentContext:
        workspace = self.conn.execute(
            "SELECT c.id conversation_id,COALESCE(v.name,'Sense assignar') venue_name,"
            "COALESCE(v.bot_knowledge,'') bot_knowledge FROM events e "
            "JOIN conversations c ON c.id=e.conversation_id "
            "LEFT JOIN venues v ON v.id=c.venue_id AND v.active=1 WHERE e.id=?",
            (event["id"],),
        ).fetchone()
        if workspace is None:
            raise RuntimeError("conversation_not_found")
        history_rows = self.conn.execute(
            "SELECT channel,body_text,received_at FROM events WHERE conversation_id=? AND id<>? "
            "ORDER BY received_at DESC,id DESC LIMIT 8",
            (workspace["conversation_id"], event["id"]),
        ).fetchall()
        history = tuple(
            ConversationTurn(
                channel=str(row["channel"]),
                body_text=self._bounded(row["body_text"], 2_000),
                received_at=str(row["received_at"]),
            )
            for row in reversed(history_rows)
        )
        incoming = self._bounded(event["body_text"], 8_000)
        return AgentContext(
            correlation_id=str(event["id"]),
            conversation_id=str(workspace["conversation_id"]),
            channel=str(event["channel"]),
            venue_name=str(workspace["venue_name"]),
            venue_knowledge=self._bounded(workspace["bot_knowledge"], 12_000),
            language_hint=detect_language_hint(incoming),
            incoming_message=incoming,
            history=history,
        )

    def _action_for_job(self, job: sqlite3.Row, event: sqlite3.Row,
                        worker_id: str) -> tuple[sqlite3.Row, str]:
        existing = self.conn.execute(
            "SELECT a.*,p.outcome FROM actions a JOIN policy_decisions p ON p.action_id=a.id "
            "WHERE a.job_id=? ORDER BY a.created_at LIMIT 1", (job["id"],),
        ).fetchone()
        if existing:
            return existing, str(existing["outcome"])
        provider_error = None
        try:
            intended = self.agent_provider.generate_action(self._agent_context(event))
        except AgentProviderError as exc:
            provider_error = exc.code
            intended = IntendedAction("escalate_to_owner", {
                "reason": "agent_provider_unavailable",
                "provider_error": exc.code,
            })
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
            if provider_error:
                record(self.conn, worker_id, "agent.generation_failed", "event", event["id"],
                       "manual_review", {"provider": self.agent_provider.provider_id,
                                         "code": provider_error})
            else:
                record(self.conn, worker_id, "agent.generation_completed", "event", event["id"],
                       "proposed", {"provider": self.agent_provider.provider_id})
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
