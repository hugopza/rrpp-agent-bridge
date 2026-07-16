from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .agent import generate_action
from .models import IntendedAction

if TYPE_CHECKING:
    from .config import Settings


@dataclass(frozen=True)
class ConversationTurn:
    channel: str
    body_text: str
    received_at: str


@dataclass(frozen=True)
class AgentContext:
    correlation_id: str
    conversation_id: str
    channel: str
    venue_name: str
    venue_knowledge: str
    language_hint: str
    incoming_message: str
    history: tuple[ConversationTurn, ...]


class AgentProviderError(RuntimeError):
    """A sanitized provider failure that is safe to persist in audit metadata."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AgentProvider(Protocol):
    provider_id: str

    def generate_action(self, context: AgentContext) -> IntendedAction:
        ...


class DeterministicAgentProvider:
    provider_id = "deterministic"

    def generate_action(self, context: AgentContext) -> IntendedAction:
        language = context.language_hint if context.language_hint in {"ca", "es"} else "ca"
        return generate_action(context.incoming_message, language)


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
