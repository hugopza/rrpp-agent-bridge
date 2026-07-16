from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from .audit import record, utc_now
from .db import transaction
from .instagram_sender import InstagramSendError, InstagramSender
from .runtime import get_mode
from .workspace import create_review


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
        timespec="milliseconds"
    )


def enqueue_delivery(conn: sqlite3.Connection, action_id: str, conversation_id: str,
                     channel: str, sender_account_id: str, recipient_external_id: str,
                     text: str, author_type: str, author_id: str) -> str:
    text = text.strip()
    if channel != "instagram" or author_type not in {"bot", "human"}:
        raise ValueError("Unsupported delivery target")
    if not text or len(text) > 1_000 or not sender_account_id or not recipient_external_id:
        raise ValueError("Invalid delivery payload")
    existing = conn.execute("SELECT id FROM deliveries WHERE action_id=?", (action_id,)).fetchone()
    if existing:
        return str(existing["id"])
    delivery_id, timestamp = _id("del"), utc_now()
    conn.execute(
        "INSERT INTO deliveries(id,action_id,conversation_id,channel,sender_account_id,"
        "recipient_external_id,body_text,author_type,author_id,status,idempotency_key,attempts,"
        "available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,0,?,?,?)",
        (delivery_id, action_id, conversation_id, channel, sender_account_id,
         recipient_external_id, text, author_type, author_id,
         f"instagram:{action_id}", timestamp, timestamp, timestamp),
    )
    record(conn, author_id, "delivery.queued", "delivery", delivery_id, "pending",
           {"action_id": action_id, "channel": channel, "author_type": author_type})
    return delivery_id


def create_human_reply(conn: sqlite3.Connection, conversation_id: str, text: str,
                       actor: str) -> str:
    text = text.strip()
    if not text or len(text) > 1_000:
        raise ValueError("La resposta ha de tenir entre 1 i 1000 caracters")
    timestamp = utc_now()
    with transaction(conn, immediate=True):
        context = conn.execute(
            "SELECT c.id,c.channel,c.external_user_id,ra.external_account_id,"
            "e.id event_id,j.id job_id FROM conversations c "
            "JOIN receiver_accounts ra ON ra.id=c.receiver_account_id "
            "JOIN events e ON e.conversation_id=c.id "
            "JOIN jobs j ON j.event_id=e.id WHERE c.id=? "
            "ORDER BY e.received_at DESC,e.id DESC LIMIT 1", (conversation_id,),
        ).fetchone()
        if not context or context["channel"] != "instagram":
            raise ValueError("La conversa no admet enviament per Instagram")
        action_id = _id("act")
        conn.execute(
            "INSERT INTO actions(id,event_id,job_id,type,payload_json,state,mode,created_at,"
            "updated_at,author_type) VALUES(?,?,?,'human_reply',?,'queued_delivery',?,?,?,?)",
            (action_id, context["event_id"], context["job_id"],
             json.dumps({"text": text}, separators=(",", ":")), get_mode(conn), timestamp,
             timestamp, "human"),
        )
        conn.execute(
            "INSERT INTO policy_decisions VALUES(?,?,?,'policy.authenticated-human.v2',?,?)",
            (_id("dec"), action_id, "allowed",
             "Authenticated human response may use the delivery queue", timestamp),
        )
        delivery_id = enqueue_delivery(
            conn, action_id, conversation_id, "instagram", context["external_account_id"],
            context["external_user_id"], text, "human", actor,
        )
        conn.execute(
            "UPDATE conversations SET status='pending_review',assigned_operator=?,updated_at=? "
            "WHERE id=?", (actor, timestamp, conversation_id),
        )
        record(conn, actor, "human.reply_created", "action", action_id, "allowed",
               {"delivery_id": delivery_id})
    return delivery_id


class DeliveryExecutor:
    def __init__(self, conn: sqlite3.Connection, sender: InstagramSender | None,
                 canary_senders: frozenset[str], lease_seconds: int = 60):
        self.conn = conn
        self.sender = sender
        self.canary_senders = canary_senders
        self.lease_seconds = lease_seconds

    def recover_stale(self, actor: str = "worker.delivery-recovery") -> int:
        timestamp = utc_now()
        with transaction(self.conn, immediate=True):
            rows = self.conn.execute(
                "SELECT id,action_id,conversation_id,attempts FROM deliveries "
                "WHERE status='sending' AND lease_expires_at<=?", (timestamp,),
            ).fetchall()
            for row in rows:
                self.conn.execute(
                    "UPDATE deliveries SET status='unknown',worker_id=NULL,lease_expires_at=NULL,"
                    "last_error_code='delivery_lease_expired',updated_at=? WHERE id=?",
                    (timestamp, row["id"]),
                )
                self.conn.execute(
                    "INSERT OR IGNORE INTO delivery_attempts VALUES(?,?,?,'unknown',?,NULL,?)",
                    (_id("datt"), row["id"], row["attempts"],
                     "delivery_lease_expired", timestamp),
                )
                self._ensure_escalation(row["action_id"], row["conversation_id"], actor)
                record(self.conn, actor, "delivery.lease_recovered", "delivery", row["id"],
                       "unknown")
        return len(rows)

    def _claim(self, worker_id: str) -> sqlite3.Row | None:
        timestamp = utc_now()
        with transaction(self.conn, immediate=True):
            row = self.conn.execute(
                "SELECT * FROM deliveries WHERE status='pending' AND available_at<=? "
                "ORDER BY created_at,id LIMIT 1", (timestamp,),
            ).fetchone()
            if not row:
                return None
            self.conn.execute(
                "UPDATE deliveries SET status='sending',attempts=attempts+1,claimed_at=?,"
                "lease_expires_at=?,worker_id=?,updated_at=? WHERE id=? AND status='pending'",
                (timestamp, _after(self.lease_seconds), worker_id, timestamp, row["id"]),
            )
        return self.conn.execute("SELECT * FROM deliveries WHERE id=?", (row["id"],)).fetchone()

    def _ensure_escalation(self, action_id: str, conversation_id: str, actor: str) -> None:
        if not self.conn.execute("SELECT 1 FROM action_reviews WHERE action_id=?", (action_id,)).fetchone():
            create_review(self.conn, action_id, "escalation", "", actor)
        self.conn.execute(
            "UPDATE conversations SET status='pending_review',bot_paused=1,"
            "pause_reason='delivery_requires_reconciliation',updated_at=? WHERE id=?",
            (utc_now(), conversation_id),
        )

    def _suppress(self, delivery: sqlite3.Row, reason: str, actor: str) -> None:
        timestamp = utc_now()
        with transaction(self.conn, immediate=True):
            self.conn.execute(
                "UPDATE deliveries SET status='suppressed',worker_id=NULL,lease_expires_at=NULL,"
                "last_error_code=?,updated_at=? WHERE id=?", (reason, timestamp, delivery["id"]),
            )
            self.conn.execute(
                "UPDATE actions SET state='suppressed',updated_at=? WHERE id=?",
                (timestamp, delivery["action_id"]),
            )
            if delivery["author_type"] == "bot":
                if not self.conn.execute(
                    "SELECT 1 FROM action_reviews WHERE action_id=?", (delivery["action_id"],)
                ).fetchone():
                    create_review(self.conn, delivery["action_id"], "draft",
                                  delivery["body_text"], actor)
                self.conn.execute(
                    "UPDATE conversations SET status='pending_review',updated_at=? WHERE id=?",
                    (timestamp, delivery["conversation_id"]),
                )
            record(self.conn, actor, "delivery.suppressed", "delivery", delivery["id"],
                   "suppressed", {"reason": reason})

    def _permission_reason(self, delivery: sqlite3.Row) -> str | None:
        context = self.conn.execute(
            "SELECT p.outcome,c.bot_paused,a.event_id,e.rowid event_rowid FROM deliveries d "
            "JOIN actions a ON a.id=d.action_id JOIN policy_decisions p ON p.action_id=a.id "
            "JOIN conversations c ON c.id=d.conversation_id "
            "JOIN events e ON e.id=a.event_id WHERE d.id=?", (delivery["id"],),
        ).fetchone()
        if not context or context["outcome"] != "allowed":
            return "policy_not_allowed"
        if delivery["author_type"] == "bot" and context["bot_paused"]:
            return "conversation_paused"
        mode = get_mode(self.conn)
        if mode in {"shadow", "dry-run"}:
            return f"mode_{mode.replace('-', '_')}"
        if mode == "canary" and delivery["recipient_external_id"].casefold() not in self.canary_senders:
            return "canary_sender_not_allowed"
        if mode not in {"canary", "live"}:
            return "invalid_mode"
        if delivery["author_type"] == "bot":
            newer = self.conn.execute(
                "SELECT 1 FROM events WHERE conversation_id=? AND rowid>? LIMIT 1",
                (delivery["conversation_id"], context["event_rowid"]),
            ).fetchone()
            if newer:
                return "newer_inbound_message"
        return None

    def run_once(self, worker_id: str) -> bool:
        self.recover_stale()
        delivery = self._claim(worker_id)
        if not delivery:
            return False
        reason = self._permission_reason(delivery)
        if reason:
            self._suppress(delivery, reason, worker_id)
            return True
        if self.sender is None:
            self._finish_error(delivery, "instagram_sender_disabled", False, worker_id)
            return True
        try:
            result = self.sender.send_text(
                str(delivery["recipient_external_id"]), str(delivery["body_text"])
            )
        except InstagramSendError as exc:
            self._finish_error(delivery, exc.code, exc.ambiguous, worker_id)
            return True
        timestamp = utc_now()
        with transaction(self.conn, immediate=True):
            self.conn.execute(
                "UPDATE deliveries SET status='sent',external_message_id=?,sent_at=?,updated_at=?,"
                "worker_id=NULL,lease_expires_at=NULL,last_error_code=NULL WHERE id=?",
                (result.message_id, timestamp, timestamp, delivery["id"]),
            )
            self.conn.execute(
                "INSERT INTO delivery_attempts VALUES(?,?,?,'sent',NULL,?,?)",
                (_id("datt"), delivery["id"], delivery["attempts"],
                 result.message_id, timestamp),
            )
            self.conn.execute(
                "INSERT INTO conversation_messages(id,conversation_id,direction,author_type,author_id,"
                "external_message_id,body_text,status,source_event_id,source_delivery_id,created_at,sent_at) "
                "VALUES(?,?,'outbound',?,?,?,?,'sent',NULL,?,?,?)",
                (_id("msg"), delivery["conversation_id"], delivery["author_type"],
                 delivery["author_id"], result.message_id, delivery["body_text"],
                 delivery["id"], timestamp, timestamp),
            )
            self.conn.execute(
                "UPDATE actions SET state='sent',updated_at=? WHERE id=?",
                (timestamp, delivery["action_id"]),
            )
            self.conn.execute(
                "UPDATE conversations SET status='resolved',last_outbound_at=?,updated_at=? WHERE id=?",
                (timestamp, timestamp, delivery["conversation_id"]),
            )
            record(self.conn, worker_id, "delivery.sent", "delivery", delivery["id"], "sent",
                   {"external_message_id": result.message_id,
                    "author_type": delivery["author_type"]})
        return True

    def _finish_error(self, delivery: sqlite3.Row, code: str, ambiguous: bool,
                      actor: str) -> None:
        status, timestamp = ("unknown" if ambiguous else "failed"), utc_now()
        with transaction(self.conn, immediate=True):
            self.conn.execute(
                "UPDATE deliveries SET status=?,last_error_code=?,worker_id=NULL,"
                "lease_expires_at=NULL,updated_at=? WHERE id=?",
                (status, code, timestamp, delivery["id"]),
            )
            self.conn.execute(
                "INSERT INTO delivery_attempts VALUES(?,?,?,?,?,NULL,?)",
                (_id("datt"), delivery["id"], delivery["attempts"], status, code, timestamp),
            )
            self.conn.execute(
                "UPDATE actions SET state='delivery_failed',updated_at=? WHERE id=?",
                (timestamp, delivery["action_id"]),
            )
            self._ensure_escalation(delivery["action_id"], delivery["conversation_id"], actor)
            record(self.conn, actor, "delivery.failed", "delivery", delivery["id"], status,
                   {"error_code": code, "ambiguous": ambiguous})
