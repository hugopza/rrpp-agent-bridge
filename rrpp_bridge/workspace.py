from __future__ import annotations

import re
import sqlite3
import uuid

from .audit import record, utc_now
from .db import transaction

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REVIEW_STATUSES = frozenset({"pending", "prepared", "rejected", "resolved"})


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def route_venue(conn: sqlite3.Connection, channel: str, recipient: str) -> str | None:
    row = conn.execute(
        "SELECT r.venue_id FROM venue_routes r JOIN venues v ON v.id=r.venue_id "
        "WHERE r.channel=? AND r.recipient=? COLLATE NOCASE AND r.active=1 AND v.active=1",
        (channel, recipient.strip()),
    ).fetchone()
    return str(row["venue_id"]) if row else None


def ensure_conversation(conn: sqlite3.Connection, channel: str, external_key: str,
                        recipient: str, message_at: str, actor: str) -> str:
    row = conn.execute(
        "SELECT * FROM conversations WHERE channel=? AND external_key=?",
        (channel, external_key),
    ).fetchone()
    timestamp = utc_now()
    routed_venue = route_venue(conn, channel, recipient)
    if row:
        venue_id = row["venue_id"] or routed_venue
        status = "open" if row["status"] == "resolved" else row["status"]
        conn.execute(
            "UPDATE conversations SET venue_id=?,status=?,last_message_at=?,updated_at=? WHERE id=?",
            (venue_id, status, max(str(row["last_message_at"]), message_at), timestamp, row["id"]),
        )
        if row["status"] == "resolved":
            record(conn, actor, "conversation.reopened", "conversation", row["id"], "open")
        return str(row["id"])
    conversation_id = _id("conv")
    conn.execute(
        "INSERT INTO conversations(id,channel,external_key,venue_id,status,last_message_at,created_at,updated_at) "
        "VALUES(?,?,?,?,'open',?,?,?)",
        (conversation_id, channel, external_key, routed_venue, message_at, timestamp, timestamp),
    )
    record(conn, actor, "conversation.created", "conversation", conversation_id, "open",
           {"channel": channel, "venue_id": routed_venue})
    return conversation_id


def create_venue(conn: sqlite3.Connection, name: str, slug: str, language: str,
                 actor: str) -> str:
    name, slug, language = name.strip(), slug.strip().casefold(), language.strip().casefold()
    if not name or len(name) > 120:
        raise ValueError("Venue name must contain 1 to 120 characters")
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("Venue slug must use lowercase letters, numbers, and hyphens")
    if language not in {"ca", "es"}:
        raise ValueError("Venue language must be ca or es")
    venue_id, timestamp = _id("ven"), utc_now()
    with transaction(conn, immediate=True):
        conn.execute(
            "INSERT INTO venues VALUES(?,?,?,?,1,?,?)",
            (venue_id, slug, name, language, timestamp, timestamp),
        )
        record(conn, actor, "venue.created", "venue", venue_id, "active",
               {"slug": slug, "language": language})
    return venue_id


def update_venue(conn: sqlite3.Connection, venue_id: str, name: str, language: str,
                 active: bool, actor: str) -> bool:
    name, language = name.strip(), language.strip().casefold()
    if not name or len(name) > 120 or language not in {"ca", "es"}:
        raise ValueError("Invalid venue name or language")
    timestamp = utc_now()
    with transaction(conn, immediate=True):
        changed = conn.execute(
            "UPDATE venues SET name=?,default_language=?,active=?,updated_at=? WHERE id=?",
            (name, language, int(active), timestamp, venue_id),
        ).rowcount
        if changed:
            record(conn, actor, "venue.updated", "venue", venue_id,
                   "active" if active else "inactive", {"language": language})
    return bool(changed)


def add_route(conn: sqlite3.Connection, venue_id: str, channel: str, recipient: str,
              actor: str) -> str:
    channel, recipient = channel.strip().casefold(), recipient.strip()
    if channel not in {"gmail", "local"} or not recipient or len(recipient) > 500:
        raise ValueError("Invalid route channel or recipient")
    route_id, timestamp = _id("route"), utc_now()
    with transaction(conn, immediate=True):
        venue = conn.execute("SELECT active FROM venues WHERE id=?", (venue_id,)).fetchone()
        if not venue:
            raise ValueError("Venue not found")
        existing = conn.execute(
            "SELECT id FROM venue_routes WHERE channel=? AND recipient=? COLLATE NOCASE",
            (channel, recipient),
        ).fetchone()
        if existing:
            route_id = str(existing["id"])
            conn.execute(
                "UPDATE venue_routes SET venue_id=?,active=1,updated_at=? WHERE id=?",
                (venue_id, timestamp, route_id),
            )
        else:
            conn.execute(
                "INSERT INTO venue_routes VALUES(?,?,?,?,1,?,?)",
                (route_id, venue_id, channel, recipient, timestamp, timestamp),
            )
        record(conn, actor, "venue.route_added", "venue", venue_id, "active",
               {"route_id": route_id, "channel": channel})
    return route_id


def disable_route(conn: sqlite3.Connection, route_id: str, actor: str) -> bool:
    with transaction(conn, immediate=True):
        route = conn.execute("SELECT * FROM venue_routes WHERE id=?", (route_id,)).fetchone()
        if not route or not route["active"]:
            return False
        conn.execute("UPDATE venue_routes SET active=0,updated_at=? WHERE id=?", (utc_now(), route_id))
        record(conn, actor, "venue.route_disabled", "venue", route["venue_id"], "inactive",
               {"route_id": route_id, "channel": route["channel"]})
    return True


def assign_conversation(conn: sqlite3.Connection, conversation_id: str,
                        venue_id: str | None, actor: str) -> bool:
    with transaction(conn, immediate=True):
        if venue_id and not conn.execute("SELECT 1 FROM venues WHERE id=? AND active=1", (venue_id,)).fetchone():
            raise ValueError("Active venue not found")
        changed = conn.execute(
            "UPDATE conversations SET venue_id=?,updated_at=? WHERE id=?",
            (venue_id, utc_now(), conversation_id),
        ).rowcount
        if changed:
            record(conn, actor, "conversation.assigned", "conversation", conversation_id,
                   "assigned" if venue_id else "unassigned", {"venue_id": venue_id})
    return bool(changed)


def set_conversation_status(conn: sqlite3.Connection, conversation_id: str,
                            status: str, actor: str) -> bool:
    if status not in {"open", "resolved"}:
        raise ValueError("Invalid operator conversation status")
    with transaction(conn, immediate=True):
        changed = conn.execute(
            "UPDATE conversations SET status=?,updated_at=? WHERE id=?",
            (status, utc_now(), conversation_id),
        ).rowcount
        if changed:
            record(conn, actor, f"conversation.{status}", "conversation", conversation_id, status)
    return bool(changed)


def create_review(conn: sqlite3.Connection, action_id: str, kind: str, text: str,
                  actor: str) -> str:
    if kind not in {"draft", "escalation"}:
        raise ValueError("Invalid review kind")
    review_id, revision_id, timestamp = _id("rev"), _id("drev"), utc_now()
    conn.execute(
        "INSERT INTO action_reviews VALUES(?,?,?,'pending',?,1,NULL,?,?)",
        (review_id, action_id, kind, text, timestamp, timestamp),
    )
    if kind == "draft":
        conn.execute(
            "INSERT INTO draft_revisions VALUES(?,?,?,?,?,?)",
            (revision_id, review_id, 1, text, actor, timestamp),
        )
    record(conn, actor, "review.created", "review", review_id, "pending",
           {"action_id": action_id, "kind": kind})
    return review_id


def edit_review(conn: sqlite3.Connection, review_id: str, text: str, actor: str) -> bool:
    text = text.strip()
    if not text or len(text) > 20_000:
        raise ValueError("Draft text must contain 1 to 20000 characters")
    with transaction(conn, immediate=True):
        review = conn.execute("SELECT * FROM action_reviews WHERE id=?", (review_id,)).fetchone()
        if not review or review["kind"] != "draft" or review["status"] != "pending":
            return False
        version, timestamp = int(review["version"]) + 1, utc_now()
        conn.execute(
            "UPDATE action_reviews SET current_text=?,version=?,reviewed_by=?,updated_at=? WHERE id=?",
            (text, version, actor, timestamp, review_id),
        )
        conn.execute(
            "INSERT INTO draft_revisions VALUES(?,?,?,?,?,?)",
            (_id("drev"), review_id, version, text, actor, timestamp),
        )
        record(conn, actor, "review.edited", "review", review_id, "pending", {"version": version})
    return True


def transition_review(conn: sqlite3.Connection, review_id: str, target: str,
                      actor: str) -> bool:
    if target not in {"prepared", "rejected", "resolved"}:
        raise ValueError("Invalid review transition")
    with transaction(conn, immediate=True):
        review = conn.execute("SELECT * FROM action_reviews WHERE id=?", (review_id,)).fetchone()
        if not review or review["status"] != "pending":
            return False
        if review["kind"] == "draft" and target == "resolved":
            raise ValueError("Drafts cannot be resolved")
        if review["kind"] == "escalation" and target != "resolved":
            raise ValueError("Escalations can only be resolved")
        timestamp = utc_now()
        conn.execute(
            "UPDATE action_reviews SET status=?,reviewed_by=?,updated_at=? WHERE id=?",
            (target, actor, timestamp, review_id),
        )
        action_state = {"prepared": "prepared", "rejected": "rejected", "resolved": "resolved"}[target]
        conn.execute(
            "UPDATE actions SET state=?,updated_at=? WHERE id=?",
            (action_state, timestamp, review["action_id"]),
        )
        event = conn.execute("SELECT event_id FROM actions WHERE id=?", (review["action_id"],)).fetchone()
        if event:
            conversation = conn.execute(
                "SELECT conversation_id FROM events WHERE id=?", (event["event_id"],)
            ).fetchone()
            pending = conn.execute(
                "SELECT count(*) FROM action_reviews r JOIN actions a ON a.id=r.action_id "
                "JOIN events e ON e.id=a.event_id WHERE e.conversation_id=? AND r.status='pending'",
                (conversation["conversation_id"],),
            ).fetchone()[0]
            conn.execute(
                "UPDATE conversations SET status=?,updated_at=? WHERE id=?",
                ("pending_review" if pending else "open", timestamp, conversation["conversation_id"]),
            )
        record(conn, actor, f"review.{target}", "review", review_id, target,
               {"action_id": review["action_id"], "version": review["version"]})
    return True
