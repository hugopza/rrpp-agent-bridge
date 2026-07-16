from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .agent import generate_action
from .models import AgentDecision, CatalogItem, IntendedAction

if TYPE_CHECKING:
    from .config import Settings


@dataclass(frozen=True)
class ConversationTurn:
    direction: str
    author_type: str
    body_text: str
    created_at: str


@dataclass(frozen=True)
class AgentContext:
    correlation_id: str
    conversation_id: str
    channel: str
    receiver_account_id: str
    external_user_id: str
    language_hint: str
    incoming_message: str
    history: tuple[ConversationTurn, ...]
    catalog_items: tuple[CatalogItem, ...]
    bot_paused: bool


class AgentProviderError(RuntimeError):
    """A sanitized provider failure that is safe to persist in audit metadata."""

    def __init__(self, code: str, diagnostic: str = ""):
        super().__init__(code)
        self.code = code
        self.diagnostic = diagnostic


class AgentProvider(Protocol):
    provider_id: str

    def generate_decision(self, context: AgentContext) -> AgentDecision:
        ...


class DeterministicAgentProvider:
    provider_id = "deterministic"

    def generate_decision(self, context: AgentContext) -> AgentDecision:
        language = context.language_hint if context.language_hint in {"ca", "es"} else "ca"
        action = generate_action(context.incoming_message, language)
        if action.type == "escalate_to_owner":
            return AgentDecision("human_required", "", language, "sensitive_request",
                                 structured=False)
        return AgentDecision(
            "human_required", str(action.payload.get("text", "")), language,
            "unstructured_provider_output", structured=False,
        )


def legacy_action_to_decision(action: IntendedAction, language: str = "unknown") -> AgentDecision:
    """Keep old fake providers and stored behavior review-only during the transition."""
    if action.type == "escalate_to_owner":
        return AgentDecision("human_required", "", language, "sensitive_request",
                             structured=False)
    if action.type == "no_action":
        return AgentDecision("ignore", "", language, "spam_or_non_message", structured=False)
    return AgentDecision(
        "human_required", str(action.payload.get("text", "")),
        str(action.payload.get("language", language)), "unstructured_provider_output",
        structured=False,
    )


def detect_language_hint(text: str) -> str:
    words = set(text.casefold().replace("?", " ").replace("!", " ").split())
    catalan = {"gràcies", "teniu", "entrades", "aquesta", "quan", "quina", "quin",
               "puc", "vull", "vosaltres", "amb", "què", "com", "avui"}
    spanish = {"gracias", "tenéis", "entradas", "esta", "cuando", "donde", "puedo",
               "quiero", "vosotros", "con", "para", "qué", "cómo", "hoy"}
    ca_score, es_score = len(words & catalan), len(words & spanish)
    if ca_score > es_score:
        return "ca"
    if es_score > ca_score:
        return "es"
    return "unknown"


def build_agent_provider(settings: Settings) -> AgentProvider:
    if not settings.openclaw_enabled:
        return DeterministicAgentProvider()
    from .openclaw_client import OpenClawAgentProvider
    return OpenClawAgentProvider(
        base_url=settings.openclaw_base_url,
        agent_id=settings.openclaw_agent_id,
        timeout_seconds=settings.openclaw_timeout_seconds,
        gateway_token=settings.openclaw_gateway_token,
    )
