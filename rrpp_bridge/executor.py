from __future__ import annotations

import json
import sqlite3
import uuid

from .action_executor import LocalActionExecutor
from .agent_provider import (AgentContext, AgentProvider, AgentProviderError, ConversationTurn,
                             DeterministicAgentProvider, detect_language_hint,
                             legacy_action_to_decision)
from .audit import record, utc_now
from .catalog import load_snapshot
from .db import transaction
from .delivery import DeliveryExecutor, enqueue_delivery
from .models import AgentDecision, IntendedAction
from .policy import Policy
from .queue import JobQueue
from .runtime import get_mode
from .workspace import create_review


class SupersededJob(RuntimeError):
    pass


class Executor:
    def __init__(self, conn: sqlite3.Connection, max_attempts: int = 3,
                 lease_seconds: int = 60, canary_senders: frozenset[str] = frozenset(),
                 agent_provider: AgentProvider | None = None,
                 instagram_sender=None):
        self.conn = conn
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.queue = JobQueue(conn)
        self.policy = Policy()
        self.action_executor = LocalActionExecutor(conn, canary_senders)
        self.agent_provider = agent_provider or DeterministicAgentProvider()
        self.delivery_executor = DeliveryExecutor(
            conn, instagram_sender, canary_senders, lease_seconds
        )

    @staticmethod
    def _bounded(value: object, limit: int) -> str:
        return str(value or "")[:limit]

    def _agent_context(self, event: sqlite3.Row) -> AgentContext:
        workspace = self.conn.execute(
            "SELECT c.id conversation_id,c.external_user_id,c.bot_paused,"
            "ra.external_account_id receiver_account_id FROM events e "
            "JOIN conversations c ON c.id=e.conversation_id "
            "JOIN receiver_accounts ra ON ra.id=c.receiver_account_id WHERE e.id=?",
            (event["id"],),
        ).fetchone()
        if workspace is None:
            raise RuntimeError("conversation_not_found")
        history_rows = self.conn.execute(
            "SELECT direction,author_type,body_text,created_at FROM conversation_messages "
            "WHERE conversation_id=? AND source_event_id IS NOT ? "
            "ORDER BY created_at DESC,id DESC LIMIT 12",
            (workspace["conversation_id"], event["id"]),
        ).fetchall()
        history = tuple(
            ConversationTurn(
                direction=str(row["direction"]), author_type=str(row["author_type"]),
                body_text=self._bounded(row["body_text"], 2_000),
                created_at=str(row["created_at"]),
            )
            for row in reversed(history_rows)
        )
        incoming = self._bounded(event["body_text"], 8_000)
        return AgentContext(
            correlation_id=str(event["id"]),
            conversation_id=str(workspace["conversation_id"]),
            channel=str(event["channel"]),
            receiver_account_id=str(workspace["receiver_account_id"]),
            external_user_id=str(workspace["external_user_id"]),
            language_hint=detect_language_hint(incoming),
            incoming_message=incoming,
            history=history,
            catalog_items=load_snapshot(self.conn),
            bot_paused=bool(workspace["bot_paused"]),
        )

    def _generate(self, context: AgentContext) -> AgentDecision:
        provider = self.agent_provider
        if hasattr(provider, "generate_decision"):
            return provider.generate_decision(context)
        if hasattr(provider, "generate_action"):
            return legacy_action_to_decision(
                provider.generate_action(context), context.language_hint
            )
        raise AgentProviderError("agent_provider_contract_invalid")

    @staticmethod
    def _intended(decision: AgentDecision, provider_id: str) -> IntendedAction:
        references = [
            {"type": item.type, "id": item.id, "verified_at": item.verified_at}
            for item in decision.referenced_items
        ]
        payload = {
            "text": decision.text,
            "language": decision.language,
            "reason_code": decision.reason_code,
            "agent_action": decision.action,
            "referenced_items": references,
            "structured": decision.structured,
            "source": provider_id,
        }
        if decision.action in {"reply", "ask_clarification"}:
            return IntendedAction("send_reply", payload)
        if decision.action == "ignore":
            return IntendedAction("no_action", payload)
        return IntendedAction("escalate_to_owner", payload)

    def _newer_inbound_exists(self, event: sqlite3.Row) -> bool:
        return bool(self.conn.execute(
            "SELECT 1 FROM events WHERE conversation_id=? AND rowid>(SELECT rowid FROM events WHERE id=?) "
            "LIMIT 1", (event["conversation_id"], event["id"]),
        ).fetchone())

    def _action_for_job(self, job: sqlite3.Row, event: sqlite3.Row,
                        worker_id: str) -> tuple[sqlite3.Row, str, bool]:
        existing = self.conn.execute(
            "SELECT a.*,p.outcome FROM actions a JOIN policy_decisions p ON p.action_id=a.id "
            "WHERE a.job_id=? ORDER BY a.created_at LIMIT 1", (job["id"],),
        ).fetchone()
        if existing:
            has_delivery = bool(self.conn.execute(
                "SELECT 1 FROM deliveries WHERE action_id=?", (existing["id"],)
            ).fetchone())
            return existing, str(existing["outcome"]), has_delivery
        provider_error = None
        context = self._agent_context(event)
        try:
            decision = self._generate(context)
        except AgentProviderError as exc:
            provider_error = exc.code
            decision = AgentDecision(
                "human_required", "", context.language_hint,
                "unknown_information", structured=False,
            )
        if self._newer_inbound_exists(event):
            raise SupersededJob("newer_inbound_message")
        intended = self._intended(decision, self.agent_provider.provider_id)
        policy = self.policy.decide(
            intended, channel=str(event["channel"]), incoming_text=str(event["body_text"]),
            bot_paused=context.bot_paused,
        )
        action_id, decision_id, timestamp = (
            f"act_{uuid.uuid4().hex}", f"dec_{uuid.uuid4().hex}", utc_now()
        )
        mode = get_mode(self.conn)
        has_delivery = False
        with transaction(self.conn, immediate=True):
            self.conn.execute(
                "INSERT INTO actions(id,event_id,job_id,type,payload_json,state,mode,created_at,"
                "updated_at,author_type) VALUES(?,?,?,?,?,'generated',?,?,?,'agent')",
                (action_id, event["id"], job["id"], intended.type,
                 json.dumps(intended.payload, separators=(",", ":")), mode, timestamp, timestamp),
            )
            self.conn.execute(
                "INSERT INTO policy_decisions VALUES(?,?,?,?,?,?)",
                (decision_id, action_id, policy.outcome, policy.policy_id, policy.reason, timestamp),
            )
            self.conn.execute(
                "INSERT INTO agent_decisions VALUES(?,?,?,?,?,?,?,?,?)",
                (f"adec_{uuid.uuid4().hex}", action_id, "1", decision.action,
                 decision.language, decision.reason_code, int(decision.structured),
                 json.dumps(intended.payload["referenced_items"], separators=(",", ":")), timestamp),
            )
            record(self.conn, worker_id, "action.generated", "action", action_id, "decided",
                   {"type": intended.type, "agent_action": decision.action})
            if provider_error:
                record(self.conn, worker_id, "agent.generation_failed", "event", event["id"],
                       "manual_review", {"provider": self.agent_provider.provider_id,
                                         "code": provider_error})
            else:
                record(self.conn, worker_id, "agent.generation_completed", "event", event["id"],
                       "proposed", {"provider": self.agent_provider.provider_id,
                                    "structured": decision.structured})
            record(self.conn, "policy", "action.decided", "action", action_id,
                   policy.outcome, {"policy_id": policy.policy_id})
            auto_mode = mode == "live" or (
                mode == "canary" and str(event["sender"]).casefold()
                in self.action_executor.canary_senders
            )
            if (policy.outcome == "allowed" and auto_mode
                    and self.delivery_executor.sender is not None):
                enqueue_delivery(
                    self.conn, action_id, str(event["conversation_id"]), "instagram",
                    str(event["recipient"]), str(event["sender"]), decision.text,
                    "bot", self.agent_provider.provider_id,
                )
                self.conn.execute(
                    "UPDATE actions SET state='queued_delivery',updated_at=? WHERE id=?",
                    (timestamp, action_id),
                )
                has_delivery = True
            elif intended.type == "no_action" and policy.outcome == "ignored":
                self.conn.execute(
                    "UPDATE actions SET state='ignored',updated_at=? WHERE id=?",
                    (timestamp, action_id),
                )
            else:
                kind = "draft" if decision.text else "escalation"
                create_review(self.conn, action_id, kind, decision.text, worker_id)
                self.conn.execute(
                    "UPDATE actions SET state='pending_review',updated_at=? WHERE id=?",
                    (timestamp, action_id),
                )
                self.conn.execute(
                    "UPDATE conversations SET status='pending_review',updated_at=? WHERE id=?",
                    (timestamp, event["conversation_id"]),
                )
        row = self.conn.execute(
            "SELECT a.*,p.outcome FROM actions a JOIN policy_decisions p ON p.action_id=a.id "
            "WHERE a.id=?", (action_id,),
        ).fetchone()
        return row, policy.outcome, has_delivery

    def _supersede_job(self, job: sqlite3.Row, event: sqlite3.Row,
                       worker_id: str) -> None:
        timestamp = utc_now()
        newer = self.conn.execute(
            "SELECT j.id FROM jobs j JOIN events e ON e.id=j.event_id "
            "WHERE e.conversation_id=? AND e.id<>? ORDER BY e.rowid DESC LIMIT 1",
            (event["conversation_id"], event["id"]),
        ).fetchone()
        with transaction(self.conn, immediate=True):
            self.conn.execute(
                "UPDATE jobs SET state='superseded',superseded_by_job_id=?,worker_id=NULL,"
                "lease_expires_at=NULL,updated_at=? WHERE id=?",
                (newer["id"] if newer else None, timestamp, job["id"]),
            )
            self.conn.execute("UPDATE events SET status='batched' WHERE id=?", (event["id"],))
            record(self.conn, worker_id, "job.superseded", "job", job["id"], "superseded",
                   {"reason": "newer_inbound_message"})

    def run_once(self, worker_id: str = "worker.local") -> bool:
        if self.delivery_executor.run_once(worker_id):
            return True
        self.queue.recover_stale(self.max_attempts)
        job = self.queue.claim_next(worker_id, self.lease_seconds)
        if job is None:
            return False
        try:
            event = self.conn.execute("SELECT * FROM events WHERE id=?", (job["event_id"],)).fetchone()
            if event is None:
                raise RuntimeError("event_not_found")
            action, outcome, has_delivery = self._action_for_job(job, event, worker_id)
            if not has_delivery and action["type"] != "no_action":
                mode = get_mode(self.conn)
                self.action_executor.execute(action, outcome, event["sender"], mode, worker_id)
            timestamp = utc_now()
            with transaction(self.conn, immediate=True):
                self.conn.execute(
                    "UPDATE jobs SET state='completed',lease_expires_at=NULL,worker_id=NULL,updated_at=? "
                    "WHERE id=?", (timestamp, job["id"]),
                )
                self.conn.execute("UPDATE events SET status='processed' WHERE id=?", (event["id"],))
                record(self.conn, worker_id, "job.completed", "job", job["id"], "completed",
                       {"delivery_queued": has_delivery})
            return True
        except SupersededJob:
            self._supersede_job(job, event, worker_id)
            return True
        except Exception as exc:
            self.queue.fail(job, exc, worker_id, self.max_attempts)
            return True
