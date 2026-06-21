from __future__ import annotations

from .models import IntendedAction


def generate_action(body: str, language: str = "ca") -> IntendedAction:
    """Deterministic placeholder agent; inbound text is data, never operational instruction."""
    lower = body.casefold()
    terms = ("vip", "taula", "mesa", "reserva", "reservation", "pagament", "pago",
             "payment", "queixa", "queja", "complaint")
    if any(term in lower for term in terms):
        return IntendedAction("escalate_to_owner", {"reason": "sensitive_or_business_request"})
    templates = {
        "ca": "Gràcies pel teu missatge. L’equip el revisarà i et respondrà aviat.",
        "es": "Gracias por tu mensaje. El equipo lo revisará y te responderá pronto.",
    }
    return IntendedAction("draft_reply", {"text": templates.get(language, templates["ca"])})
