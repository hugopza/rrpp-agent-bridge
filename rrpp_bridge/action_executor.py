from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from .audit import record, utc_now
from .db import transaction


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    reason: str


def decide_execution(mode: str, policy_outcome: str, sender: str,
                     canary_senders: frozenset[str]) -> ExecutionResult:
    if policy_outcome != "allowed":
        return ExecutionResult("suppressed", f"policy_{policy_outcome}")
    if mode == "shadow":
        return ExecutionResult("suppressed", "mode_shadow")
    if mode == "dry-run":
        return ExecutionResult("suppressed", "mode_dry_run")
    if mode == "canary" and sender.casefold() not in canary_senders:
        return ExecutionResult("suppressed", "canary_sender_not_allowed")
    if mode in {"canary", "live"}:
        return ExecutionResult("executed", "local_simulated_sink")
    return ExecutionResult("suppressed", "invalid_mode_fail_closed")


class LocalActionExecutor:
    """Network-free V1 sink. Every execution record is explicitly simulated."""

    def __init__(self, conn: sqlite3.Connection, canary_senders: frozenset[str]):
        self.conn = conn
        self.canary_senders = canary_senders

    def execute(self, action: sqlite3.Row, policy_outcome: str, sender: str,
                mode: str, actor: str) -> ExecutionResult:
        key = f"local:{action['id']}"
        existing = self.conn.execute(
            "SELECT status,reason FROM action_executions WHERE idempotency_key=?", (key,)
        ).fetchone()
        if existing:
            return ExecutionResult(str(existing["status"]), str(existing["reason"]))
        result = decide_execution(mode, policy_outcome, sender, self.canary_senders)
        timestamp = utc_now()
        pending_review = self.conn.execute(
            "SELECT 1 FROM action_reviews WHERE action_id=? AND status='pending'", (action["id"],)
        ).fetchone()
        if policy_outcome in {"pending_approval", "escalated"} or pending_review:
            action_state = "pending_review"
        else:
            action_state = "executed_simulated" if result.status == "executed" else "suppressed"
        with transaction(self.conn, immediate=True):
            self.conn.execute(
                "INSERT INTO action_executions VALUES(?,?,?,?,?,?,?,?)",
                (f"exe_{uuid.uuid4().hex}", action["id"], key, mode, result.status,
                 result.reason, 1, timestamp),
            )
            self.conn.execute(
                "UPDATE actions SET state=?,mode=?,updated_at=? WHERE id=?",
                (action_state, mode, timestamp, action["id"]),
            )
            record(self.conn, actor, "action.execution", "action", action["id"], result.status,
                   {"mode": mode, "reason": result.reason, "simulated": True})
        return result
