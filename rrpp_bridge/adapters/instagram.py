from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from datetime import datetime, timezone
from typing import Any

from ..models import NormalizedEvent


def _timestamp(value: object) -> str:
    if not isinstance(value, (int, float)) or value < 0:
        raise ValueError("Instagram message has an invalid timestamp")
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="milliseconds")


def _receiver_account(entry_id: str, recipient_id: str,
                      allowed_accounts: frozenset[str]) -> tuple[str, str | None]:
    """Resolve an explicitly configured receiver from either Meta routing field.

    Meta's documented message envelope repeats the professional account ID in
    ``entry.id`` and ``messaging[].recipient.id``. Checking both avoids dropping
    a valid signed delivery when one integration surface exposes a different ID,
    while still failing closed unless one of those IDs is configured. If both
    fields name different configured accounts, routing is ambiguous and rejected.
    """
    matches = {value for value in (entry_id, recipient_id) if value in allowed_accounts}
    if len(matches) > 1:
        return "", "account_id_mismatch"
    if not matches:
        return "", "account_not_configured"
    return matches.pop(), None


def normalize(
    payload: dict[str, Any], webhook_account_ids: Collection[str] | str
) -> tuple[list[NormalizedEvent], dict[str, Any], dict[str, int]]:
    if payload.get("object") != "instagram" or not isinstance(payload.get("entry"), list):
        raise ValueError("Unsupported Instagram webhook payload")
    allowed_accounts = (
        frozenset({webhook_account_ids})
        if isinstance(webhook_account_ids, str)
        else frozenset(webhook_account_ids)
    )
    events: list[NormalizedEvent] = []
    sanitized_entries: list[dict[str, Any]] = []
    ignored_reasons: Counter[str] = Counter()
    for entry in payload["entry"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("messaging", []), list):
            raise ValueError("Malformed Instagram webhook entry")
        entry_id = str(entry.get("id") or "").strip()
        safe_messages: list[dict[str, Any]] = []
        for item in entry.get("messaging", []):
            if not isinstance(item, dict):
                raise ValueError("Malformed Instagram messaging event")
            sender = str((item.get("sender") or {}).get("id") or "").strip()
            recipient_id = str((item.get("recipient") or {}).get("id") or "").strip()
            message = item.get("message")
            if not isinstance(message, dict):
                ignored_reasons["unsupported_event"] += 1
                continue
            if message.get("is_echo") is True:
                ignored_reasons["echo_message"] += 1
                continue
            recipient, account_error = _receiver_account(
                entry_id, recipient_id, allowed_accounts
            )
            if account_error:
                ignored_reasons[account_error] += 1
                continue
            if sender in allowed_accounts or sender == recipient:
                ignored_reasons["self_message"] += 1
                continue
            message_id = str(message.get("mid") or "").strip()
            text = message.get("text")
            if not sender:
                ignored_reasons["missing_sender"] += 1
                continue
            if not message_id:
                ignored_reasons["missing_message_id"] += 1
                continue
            if not isinstance(text, str) or not text.strip():
                ignored_reasons["unsupported_message_content"] += 1
                continue
            if (len(sender) > 200 or len(recipient) > 200 or len(recipient_id) > 200
                    or len(entry_id) > 200 or len(message_id) > 500 or len(text) > 20_000):
                raise ValueError("Instagram message exceeds accepted limits")
            received_at = _timestamp(item.get("timestamp"))
            conversation_key = f"instagram:{recipient}:{sender}"
            safe_messages.append({
                "sender_id": sender,
                "provider_recipient_id": recipient_id,
                "receiver_account_id": recipient,
                "message_id": message_id,
                "text": text,
                "timestamp": item["timestamp"],
            })
            events.append(NormalizedEvent(
                channel="instagram", external_message_id=message_id, sender=sender,
                recipient=recipient, subject="Instagram DM", body_text=text.strip(),
                work_key=conversation_key, received_at=received_at,
                metadata={"native_message_id": message_id,
                          "native_conversation_id": conversation_key,
                          "instagram_sender_id": sender,
                          "instagram_recipient_id": recipient,
                          "raw_payload_reference": f"instagram:{message_id}"},
            ))
        sanitized_entries.append({"entry_id": entry_id,
                                  "messages": safe_messages})
    reasons = dict(sorted(ignored_reasons.items()))
    return events, {
        "object": "instagram", "entries": sanitized_entries,
        "ignored_reasons": reasons,
    }, reasons
