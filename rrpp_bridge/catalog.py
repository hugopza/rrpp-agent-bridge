from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime
from urllib.parse import urlparse

from .audit import record, utc_now
from .db import transaction
from .models import CatalogItem

VALID_AVAILABILITY = frozenset({"available", "sold_out", "unknown"})
VALID_EVENT_STATES = frozenset({"scheduled", "cancelled", "completed"})


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _datetime(value: str, *, optional: bool = False) -> str | None:
    value = value.strip()
    if not value and optional:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("La data no te un format valid") from exc
    if parsed.tzinfo is None:
        raise ValueError("La data ha d'incloure zona horaria")
    return parsed.isoformat(timespec="minutes")


def _money(value: str) -> int | None:
    value = value.strip().replace(",", ".")
    if not value:
        return None
    if not re.fullmatch(r"\d{1,7}(?:\.\d{1,2})?", value):
        raise ValueError("El preu ha de ser un import positiu amb maxim dos decimals")
    return int(round(float(value) * 100))


def _verified_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("L'enllac ha de ser una URL HTTPS valida")
    if len(value) > 2_000:
        raise ValueError("L'enllac es massa llarg")
    return value


def create_event(conn: sqlite3.Connection, venue_id: str, name: str, starts_at: str,
                 ends_at: str, actor: str) -> str:
    name = name.strip()
    if not name or len(name) > 160:
        raise ValueError("El nom de l'esdeveniment es obligatori")
    starts = _datetime(starts_at)
    ends = _datetime(ends_at, optional=True)
    if ends and datetime.fromisoformat(ends) <= datetime.fromisoformat(starts):
        raise ValueError("La data final ha de ser posterior a l'inici")
    event_id, timestamp = _id("cevt"), utc_now()
    with transaction(conn, immediate=True):
        if not conn.execute("SELECT 1 FROM venues WHERE id=? AND active=1", (venue_id,)).fetchone():
            raise ValueError("Discoteca activa no trobada")
        conn.execute(
            "INSERT INTO catalog_events VALUES(?,?,?,?,?,'scheduled',1,?,?,?,?)",
            (event_id, venue_id, name, starts, ends, timestamp, actor, timestamp, timestamp),
        )
        record(conn, actor, "catalog.event_created", "catalog_event", event_id, "active",
               {"venue_id": venue_id})
    return event_id


def create_offer(conn: sqlite3.Connection, event_id: str, name: str, ticket_type: str,
                 price: str, currency: str, promotion_text: str, conditions: str,
                 availability: str, link: str, actor: str) -> str:
    name, ticket_type = name.strip(), ticket_type.strip()
    promotion_text, conditions = promotion_text.strip(), conditions.strip()
    currency, availability = currency.strip().upper(), availability.strip().casefold()
    if not name or len(name) > 160 or not ticket_type or len(ticket_type) > 120:
        raise ValueError("L'oferta i el tipus d'entrada son obligatoris")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("La moneda ha de tenir tres lletres, per exemple EUR")
    if availability not in VALID_AVAILABILITY:
        raise ValueError("Disponibilitat no valida")
    if len(promotion_text) > 2_000 or len(conditions) > 4_000:
        raise ValueError("La promocio o les condicions son massa llargues")
    price_minor, verified_link = _money(price), _verified_url(link)
    offer_id, timestamp = _id("offer"), utc_now()
    with transaction(conn, immediate=True):
        if not conn.execute("SELECT 1 FROM catalog_events WHERE id=? AND active=1", (event_id,)).fetchone():
            raise ValueError("Esdeveniment actiu no trobat")
        conn.execute(
            "INSERT INTO catalog_offers VALUES(?,?,?,?,?,?,?,?,?,1,NULL,NULL,?,?,?,?)",
            (offer_id, event_id, name, ticket_type, price_minor, currency, promotion_text,
             conditions, availability, timestamp, actor, timestamp, timestamp),
        )
        if verified_link:
            conn.execute(
                "INSERT INTO catalog_links VALUES(?,?,'purchase',?,1,?,?,?,?)",
                (_id("link"), offer_id, verified_link, timestamp, actor, timestamp, timestamp),
            )
        record(conn, actor, "catalog.offer_created", "catalog_offer", offer_id, "active",
               {"event_id": event_id, "availability": availability})
    return offer_id


def load_snapshot(conn: sqlite3.Connection, *, max_items: int = 50) -> tuple[CatalogItem, ...]:
    items: list[CatalogItem] = []
    venues = conn.execute(
        "SELECT id,name,bot_knowledge,updated_at FROM venues WHERE active=1 ORDER BY name LIMIT 20"
    ).fetchall()
    for row in venues:
        items.append(CatalogItem(
            "venue", str(row["id"]), str(row["updated_at"]),
            {"name": str(row["name"]), "verified_notes": str(row["bot_knowledge"] or "")[:4_000]},
        ))
    rows = conn.execute(
        "SELECT ce.*,v.name venue_name FROM catalog_events ce JOIN venues v ON v.id=ce.venue_id "
        "WHERE ce.active=1 AND v.active=1 AND ce.status='scheduled' "
        "ORDER BY ce.starts_at LIMIT 30"
    ).fetchall()
    for row in rows:
        items.append(CatalogItem(
            "event", str(row["id"]), str(row["verified_at"]),
            {"venue_id": str(row["venue_id"]), "venue_name": str(row["venue_name"]),
             "name": str(row["name"]), "starts_at": str(row["starts_at"]),
             "ends_at": str(row["ends_at"] or ""), "status": str(row["status"])},
        ))
    rows = conn.execute(
        "SELECT o.*,ce.name event_name,ce.venue_id,v.name venue_name,"
        "(SELECT url FROM catalog_links l WHERE l.offer_id=o.id AND l.active=1 "
        " ORDER BY l.created_at DESC LIMIT 1) purchase_url "
        "FROM catalog_offers o JOIN catalog_events ce ON ce.id=o.event_id "
        "JOIN venues v ON v.id=ce.venue_id WHERE o.active=1 AND ce.active=1 AND v.active=1 "
        "ORDER BY ce.starts_at,o.price_minor LIMIT 40"
    ).fetchall()
    for row in rows:
        items.append(CatalogItem(
            "offer", str(row["id"]), str(row["verified_at"]),
            {"event_id": str(row["event_id"]), "event_name": str(row["event_name"]),
             "venue_id": str(row["venue_id"]), "venue_name": str(row["venue_name"]),
             "name": str(row["name"]), "ticket_type": str(row["ticket_type"]),
             "price_minor": row["price_minor"], "currency": str(row["currency"]),
             "promotion": str(row["promotion_text"]), "conditions": str(row["conditions"]),
             "availability": str(row["availability_status"]),
             "purchase_url": str(row["purchase_url"] or "")},
        ))
    return tuple(items[:max_items])
