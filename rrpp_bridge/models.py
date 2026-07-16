from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NormalizedEvent:
    channel: str
    external_message_id: str
    sender: str
    recipient: str
    subject: str
    body_text: str
    work_key: str
    metadata: dict[str, Any] = field(default_factory=dict)
    received_at: str | None = None


@dataclass(frozen=True)
class IntendedAction:
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ReferencedItem:
    type: str
    id: str
    verified_at: str


@dataclass(frozen=True)
class AgentDecision:
    action: str
    text: str
    language: str
    reason_code: str
    referenced_items: tuple[ReferencedItem, ...] = ()
    structured: bool = True


@dataclass(frozen=True)
class CatalogItem:
    type: str
    id: str
    verified_at: str
    data: dict[str, Any]


@dataclass(frozen=True)
class PolicyDecision:
    outcome: str
    policy_id: str
    reason: str
