from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .observability import emit


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def record(conn: sqlite3.Connection, actor: str, operation: str, entity_type: str,
           entity_id: str, outcome: str, details: dict[str, Any] | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log(occurred_at,actor,operation,entity_type,entity_id,outcome,details_json) "
        "VALUES(?,?,?,?,?,?,?)",
        (utc_now(), actor, operation, entity_type, entity_id, outcome,
         json.dumps(details or {}, separators=(",", ":"))),
    )
    log_fields: dict[str, Any] = {
        "actor": actor, "operation": operation, "entity_type": entity_type,
        "entity_id": entity_id, "outcome": outcome,
    }
    reason_code = (details or {}).get("reason_code")
    if isinstance(reason_code, str):
        log_fields["reason_code"] = reason_code
    emit("audit", **log_fields)
