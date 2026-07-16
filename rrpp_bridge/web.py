from __future__ import annotations

import hashlib
import hmac
import html
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from http import cookies
from importlib import resources
from urllib.parse import parse_qs, quote, urlencode
from wsgiref.util import setup_testing_defaults

from .config import Settings
from .catalog import create_event, create_offer
from .db import connect, current_version, latest_version, prepare_runtime
from .delivery import create_human_reply
from .queue import JobQueue
from .runtime import get_mode, initialize_mode, set_mode
from .operations import service_health
from .service import ingest_local
from .workspace import (add_route, assign_conversation, create_venue, disable_route, edit_review,
                        set_bot_paused, set_conversation_status, transition_review, update_venue)


def _escape(value: object) -> str:
    return html.escape(str(value or ""))


class Application:
    def __init__(self, settings: Settings):
        self.settings = settings
        conn = self._connect()
        try:
            prepare_runtime(conn)
            initialize_mode(conn, settings.mode)
        finally:
            conn.close()
        self.styles = resources.files("rrpp_bridge.static").joinpath("dashboard.css").read_text(
            encoding="utf-8"
        )

    def _connect(self):
        return connect(self.settings.database_path)

    def _sign(self, value: str) -> str:
        digest = hmac.new(self.settings.session_secret.encode(), value.encode(), hashlib.sha256).hexdigest()
        return f"{value}.{digest}"

    def _session(self, environ: dict) -> tuple[bool, str]:
        morsel = cookies.SimpleCookie(environ.get("HTTP_COOKIE", "")).get("rrpp_session")
        if not morsel:
            return False, ""
        try:
            user, expiry, csrf, signature = morsel.value.split(".", 3)
            raw = f"{user}.{expiry}.{csrf}"
            expected = self._sign(raw).rsplit(".", 1)[1]
            valid = hmac.compare_digest(signature, expected) and int(expiry) >= int(time.time())
            return valid and hmac.compare_digest(user, self.settings.dashboard_user), csrf
        except (ValueError, TypeError):
            return False, ""

    @staticmethod
    def _body(environ: dict, limit: int = 25_000) -> dict[str, str]:
        if environ.get("CONTENT_TYPE", "application/x-www-form-urlencoded").split(";", 1)[0] != "application/x-www-form-urlencoded":
            raise ValueError("Unsupported content type")
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError as exc:
            raise ValueError("Invalid content length") from exc
        if length < 0 or length > limit:
            raise ValueError("Request body is too large")
        raw = environ["wsgi.input"].read(length).decode("utf-8")
        return {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}

    @staticmethod
    def _respond(start_response, status: str, body: str, headers=None,
                 content_type: str = "text/html; charset=utf-8"):
        encoded = body.encode("utf-8")
        base = [("Content-Type", content_type), ("Content-Length", str(len(encoded))),
                ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                ("Content-Security-Policy", "default-src 'self'; style-src 'self'"),
                ("Cache-Control", "no-store")]
        start_response(status, base + (headers or []))
        return [encoded]

    def _csrf_form(self, environ: dict, csrf: str) -> dict[str, str]:
        form = self._body(environ)
        if not hmac.compare_digest(form.get("csrf", ""), csrf):
            raise PermissionError("Invalid CSRF token")
        return form

    @staticmethod
    def _query(environ: dict) -> dict[str, str]:
        return {key: values[0] for key, values in parse_qs(
            environ.get("QUERY_STRING", ""), keep_blank_values=True
        ).items()}

    @staticmethod
    def _brand() -> str:
        return """<a class="brand" href="/" aria-label="RRPP Agent Bridge, inici">
  <span class="brand-mark" aria-hidden="true">RB</span>
  <span class="brand-copy"><strong>RRPP Agent Bridge</strong><span>Operacions i auditoria</span></span>
</a>"""

    @staticmethod
    def _badge(value: object, label: str | None = None) -> str:
        text = str(value or "unknown")
        css = re.sub(r"[^a-z0-9_-]", "-", text.casefold())
        channel_labels = {"instagram": "Instagram", "local": "Simulador"}
        return f'<span class="badge {css}">{_escape(label or channel_labels.get(text, text.replace("_", " ")))}</span>'

    def _header(self, csrf: str, active: str = "summary") -> str:
        links = (("summary", "/", "Resum"), ("conversations", "/conversations", "Converses"),
                 ("reviews", "/reviews", "Revisió"), ("activity", "/activity", "Activitat"),
                 ("venues", "/venues", "Discoteques"), ("system", "/system", "Sistema"))
        nav = "".join(
            f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
            for key, href, label in links
        )
        return f"""<header class="topbar">{self._brand()}
  <nav class="main-nav" aria-label="Navegació principal">{nav}</nav>
  <div class="topbar-actions"><form method="post" action="/logout" class="inline-form">
    <input type="hidden" name="csrf" value="{_escape(csrf)}"><button class="secondary">Tancar sessió</button>
  </form></div></header>"""

    def _layout(self, title: str, csrf: str, active: str, body: str) -> str:
        return self._page(title, f'<div class="app-shell">{self._header(csrf, active)}<main>{body}</main>'
                          '<footer class="footer">RRPP Agent Bridge · operacions segures i auditables</footer></div>')

    @staticmethod
    def _short_id(value: object) -> str:
        text = str(value or "")
        short = f"…{text[-10:]}" if len(text) > 13 else text
        return f'<span title="{_escape(text)}">{_escape(short)}</span>'

    @staticmethod
    def _time(value: object) -> str:
        text = str(value or "")
        visible = text.replace("T", " ")[:19] if text else "—"
        return f'<time title="{_escape(text)}">{_escape(visible)}</time>'

    def __call__(self, environ, start_response):
        setup_testing_defaults(environ)
        path, method = environ["PATH_INFO"], environ["REQUEST_METHOD"]
        if path == "/assets/dashboard.css" and method == "GET":
            return self._respond(start_response, "200 OK", self.styles,
                                 content_type="text/css; charset=utf-8")
        authenticated, csrf = self._session(environ)
        if path == "/login":
            return self._login(environ, start_response, method)
        if not authenticated:
            return self._respond(start_response, "303 See Other", "", [("Location", "/login")])
        try:
            if path == "/logout" and method == "POST":
                self._csrf_form(environ, csrf)
                return self._respond(start_response, "303 See Other", "", [
                    ("Location", "/login"),
                    ("Set-Cookie", "rrpp_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"),
                ])
            if path == "/simulate" and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    event_id, created = ingest_local(
                        conn, form, self.settings.response_debounce_seconds
                    )
                finally:
                    conn.close()
                return self._respond(start_response, "303 See Other", "", [
                    ("Location", f"/?notice={'accepted' if created else 'duplicate'}:{event_id}")])
            if path == "/admin/mode" and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    set_mode(conn, form.get("mode", ""), f"dashboard:{self.settings.dashboard_user}")
                finally:
                    conn.close()
                return self._respond(start_response, "303 See Other", "", [("Location", "/")])
            match = re.fullmatch(r"/admin/jobs/([A-Za-z0-9_-]+)/(retry|dismiss)", path)
            if match and method == "POST":
                self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    queue, actor = JobQueue(conn), f"dashboard:{self.settings.dashboard_user}"
                    changed = queue.retry(match.group(1), actor) if match.group(2) == "retry" else queue.dismiss(match.group(1), actor)
                finally:
                    conn.close()
                if not changed:
                    return self._respond(start_response, "409 Conflict", self._page("Not allowed", "Job is not in dead letter"))
                return self._respond(start_response, "303 See Other", "", [("Location", "/")])
            match = re.fullmatch(r"/(events|jobs|actions)/([A-Za-z0-9_-]+)", path)
            if match and method == "GET":
                return self._detail(start_response, match.group(1), match.group(2))
            match = re.fullmatch(
                r"/conversations/([A-Za-z0-9_-]+)/(assign|resolve|reopen|pause|resume|reply)", path
            )
            if match and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    actor = f"dashboard:{self.settings.dashboard_user}"
                    operation = match.group(2)
                    if operation == "assign":
                        venue_id = form.get("venue_id", "") or None
                        changed = assign_conversation(conn, match.group(1), venue_id, actor)
                    elif operation == "pause":
                        changed = set_bot_paused(
                            conn, match.group(1), True, actor, form.get("reason", "")
                        )
                    elif operation == "resume":
                        changed = set_bot_paused(conn, match.group(1), False, actor)
                    elif operation == "reply":
                        create_human_reply(conn, match.group(1), form.get("text", ""), actor)
                        changed = True
                    else:
                        status = "resolved" if operation == "resolve" else "open"
                        changed = set_conversation_status(conn, match.group(1), status, actor)
                finally:
                    conn.close()
                if not changed:
                    return self._respond(start_response, "404 Not Found", self._page("No trobat", "Conversa desconeguda"))
                return self._respond(start_response, "303 See Other", "", [("Location", f"/conversations/{match.group(1)}")])
            match = re.fullmatch(r"/conversations/([A-Za-z0-9_-]+)", path)
            if match and method == "GET":
                return self._conversation(start_response, csrf, match.group(1))
            if path == "/conversations" and method == "GET":
                return self._conversations(start_response, csrf, self._query(environ))
            match = re.fullmatch(r"/reviews/([A-Za-z0-9_-]+)/(edit|send|reject|resolve)", path)
            if match and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    actor = f"dashboard:{self.settings.dashboard_user}"
                    operation = match.group(2)
                    if operation == "edit":
                        changed = edit_review(conn, match.group(1), form.get("text", ""), actor)
                    elif operation == "send":
                        review = conn.execute(
                            "SELECT r.current_text,e.conversation_id FROM action_reviews r "
                            "JOIN actions a ON a.id=r.action_id JOIN events e ON e.id=a.event_id "
                            "WHERE r.id=? AND r.status='pending' AND r.kind='draft'",
                            (match.group(1),),
                        ).fetchone()
                        if review:
                            create_human_reply(
                                conn, review["conversation_id"], review["current_text"], actor
                            )
                            changed = transition_review(
                                conn, match.group(1), "prepared", actor
                            )
                        else:
                            changed = False
                    else:
                        changed = transition_review(
                            conn, match.group(1),
                            {"reject": "rejected", "resolve": "resolved"}[operation], actor,
                        )
                finally:
                    conn.close()
                if not changed:
                    return self._respond(start_response, "409 Conflict", self._page("No permès", "La revisió ja no està pendent"))
                return self._respond(start_response, "303 See Other", "", [("Location", "/reviews")])
            if path == "/reviews" and method == "GET":
                return self._reviews(start_response, csrf, self._query(environ))
            if path == "/activity" and method == "GET":
                return self._activity(start_response, csrf, self._query(environ))
            if path == "/catalog/events" and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    create_event(
                        conn, form.get("venue_id", ""), form.get("name", ""),
                        form.get("starts_at", ""), form.get("ends_at", ""),
                        f"dashboard:{self.settings.dashboard_user}",
                    )
                finally:
                    conn.close()
                return self._respond(start_response, "303 See Other", "", [("Location", "/venues")])
            if path == "/catalog/offers" and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    create_offer(
                        conn, form.get("event_id", ""), form.get("name", ""),
                        form.get("ticket_type", ""), form.get("price", ""),
                        form.get("currency", "EUR"), form.get("promotion_text", ""),
                        form.get("conditions", ""), form.get("availability", "unknown"),
                        form.get("link", ""), f"dashboard:{self.settings.dashboard_user}",
                    )
                finally:
                    conn.close()
                return self._respond(start_response, "303 See Other", "", [("Location", "/venues")])
            if path == "/venues" and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    create_venue(conn, form.get("name", ""), form.get("slug", ""),
                                 "ca", f"dashboard:{self.settings.dashboard_user}",
                                 form.get("bot_knowledge", ""))
                finally:
                    conn.close()
                return self._respond(start_response, "303 See Other", "", [("Location", "/venues")])
            match = re.fullmatch(r"/venues/([A-Za-z0-9_-]+)/(update|routes)", path)
            if match and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    actor = f"dashboard:{self.settings.dashboard_user}"
                    if match.group(2) == "update":
                        changed = update_venue(conn, match.group(1), form.get("name", ""),
                                               None, form.get("active") == "1", actor,
                                               form.get("bot_knowledge", ""))
                    else:
                        add_route(conn, match.group(1), form.get("channel", ""), form.get("recipient", ""), actor)
                        changed = True
                finally:
                    conn.close()
                if not changed:
                    return self._respond(start_response, "404 Not Found", self._page("No trobat", "Discoteca desconeguda"))
                return self._respond(start_response, "303 See Other", "", [("Location", "/venues")])
            if path == "/venues" and method == "GET":
                return self._venues(start_response, csrf)
            if path == "/system" and method == "GET":
                return self._system(start_response, csrf)
            match = re.fullmatch(r"/routes/([A-Za-z0-9_-]+)/disable", path)
            if match and method == "POST":
                self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    changed = disable_route(conn, match.group(1), f"dashboard:{self.settings.dashboard_user}")
                finally:
                    conn.close()
                if not changed:
                    return self._respond(start_response, "409 Conflict", self._page("No permès", "La regla no està activa"))
                return self._respond(start_response, "303 See Other", "", [("Location", "/venues")])
            if path == "/" and method == "GET":
                return self._dashboard(start_response, csrf)
        except PermissionError as exc:
            return self._respond(start_response, "403 Forbidden", self._page("Forbidden", _escape(exc)))
        except ValueError as exc:
            return self._respond(start_response, "400 Bad Request", self._page("Invalid request", _escape(exc)))
        return self._respond(start_response, "404 Not Found", self._page("Not found", "Unknown route"))

    def _login(self, environ, start_response, method: str):
        error = ""
        if method == "POST":
            try:
                form = self._body(environ, 4_096)
                user_ok = hmac.compare_digest(form.get("username", ""), self.settings.dashboard_user)
                pass_ok = hmac.compare_digest(form.get("password", ""), self.settings.dashboard_password)
                if user_ok and pass_ok:
                    csrf = secrets.token_urlsafe(24)
                    raw = f"{self.settings.dashboard_user}.{int(time.time()) + 28_800}.{csrf}"
                    secure = "; Secure" if environ.get("wsgi.url_scheme") == "https" else ""
                    return self._respond(start_response, "303 See Other", "", [
                        ("Location", "/"),
                        ("Set-Cookie", f"rrpp_session={self._sign(raw)}; Path=/; HttpOnly; SameSite=Strict{secure}"),
                    ])
                error = "Usuari o contrasenya incorrectes"
            except ValueError as exc:
                error = str(exc)
        error_box = f'<p class="error-box" role="alert">{_escape(error)}</p>' if error else ""
        content = f"""
<main class="login-shell">
  <section class="login-card" aria-labelledby="login-title">
    {self._brand()}
    <p class="eyebrow">Accés privat</p>
    <h1 id="login-title">Centre de control</h1>
    <p>Supervisa els missatges, les decisions i cada acció del sistema.</p>
    <form method="post" class="login-form">
      <label>Usuari
        <input name="username" required autocomplete="username" autofocus>
      </label>
      <label>Contrasenya
        <input name="password" type="password" required autocomplete="current-password">
      </label>
      <button type="submit">Entrar al dashboard</button>
    </form>
    {error_box}
  </section>
</main>"""
        return self._respond(start_response, "200 OK", self._page("Accés privat", content))

    def _dashboard_legacy(self, start_response, csrf: str):
        conn = self._connect()
        try:
            mode = get_mode(conn)
            job_counts = {row["state"]: row["n"] for row in conn.execute("SELECT state,count(*) n FROM jobs GROUP BY state")}
            execution_counts = {row["status"]: row["n"] for row in conn.execute("SELECT status,count(*) n FROM action_executions GROUP BY status")}
            conversation_counts = {row["status"]: row["n"] for row in conn.execute("SELECT status,count(*) n FROM conversations GROUP BY status")}
            unassigned = conn.execute("SELECT count(*) FROM conversations WHERE venue_id IS NULL").fetchone()[0]
            pending_reviews = conn.execute("SELECT count(*) FROM action_reviews WHERE status='pending'").fetchone()[0]
            conversations = conn.execute(
                "SELECT c.*,v.name venue_name,(SELECT sender FROM events e WHERE e.conversation_id=c.id ORDER BY received_at DESC,id DESC LIMIT 1) sender,"
                "(SELECT subject FROM events e WHERE e.conversation_id=c.id ORDER BY received_at DESC,id DESC LIMIT 1) subject "
                "FROM conversations c LEFT JOIN venues v ON v.id=c.venue_id ORDER BY c.last_message_at DESC,c.id DESC LIMIT 8"
            ).fetchall()
            audits = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 10").fetchall()
            failures = conn.execute("SELECT * FROM jobs WHERE state='dead_letter' ORDER BY updated_at DESC LIMIT 20").fetchall()
            gmail_state = conn.execute(
                "SELECT updated_at FROM connector_state WHERE connector='gmail' AND key='history_id'"
            ).fetchone()
            service_rows = conn.execute("SELECT * FROM service_status ORDER BY service").fetchall()
            last_backup = conn.execute(
                "SELECT * FROM backup_records WHERE integrity_status='verified' AND kind IN ('daily','monthly') "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        metric_values = {
            "open": ("Converses obertes", conversation_counts.get("open", 0)),
            "pending_review": ("Pendents de revisió", pending_reviews),
            "unassigned": ("Sense assignar", unassigned),
            "dead_letter": ("Jobs fallits", job_counts.get("dead_letter", 0)),
        }
        metrics = "".join(
            f'<article class="card metric"><span class="metric-label">{label}</span>'
            f'<strong class="metric-value">{value}</strong>{self._badge(key)}</article>'
            for key, (label, value) in metric_values.items()
        )
        conversation_rows = "".join(
            f"<tr><td><a href='/conversations/{_escape(c['id'])}'><span class='cell-main'><strong>{_escape(c['subject'] or 'Sense assumpte')}</strong>"
            f"<small>{_escape(c['sender'])}</small></span></a></td><td>{self._badge(c['channel'])}</td>"
            f"<td>{_escape(c['venue_name'] or 'Sense assignar')}</td><td>{self._badge(c['status'])}</td>"
            f"<td>{self._time(c['last_message_at'])}</td></tr>" for c in conversations
        ) or '<tr><td class="empty" colspan="5">Encara no hi ha converses.</td></tr>'
        failure_rows = "".join(
            f"<tr><td><a class='id-link' href='/jobs/{_escape(j['id'])}'>{self._short_id(j['id'])}</a></td>"
            f"<td>{j['attempts']}</td><td><span class='cell-main'><strong>{_escape(j['last_error_code'])}</strong>"
            f"<small>{_escape(j['last_error_message'])}</small></span></td>"
            f"<td><div class='table-actions'><form method='post' action='/admin/jobs/{_escape(j['id'])}/retry'>"
            f"<input type='hidden' name='csrf' value='{_escape(csrf)}'><button>Reintentar</button></form>"
            f"<form method='post' action='/admin/jobs/{_escape(j['id'])}/dismiss'>"
            f"<input type='hidden' name='csrf' value='{_escape(csrf)}'><button class='danger'>Descartar</button>"
            f"</form></div></td></tr>" for j in failures
        ) or '<tr><td class="empty" colspan="4">No hi ha jobs fallits.</td></tr>'
        audit_items = "".join(
            f"<article class='activity-item'><span class='activity-dot'></span><span class='activity-copy'>"
            f"<strong>{_escape(a['operation'].replace('.', ' · '))}</strong>"
            f"<small>{_escape(a['actor'])} · {_escape(a['entity_type'])}</small></span>"
            f"<span class='activity-time'>{self._time(a['occurred_at'])}</span></article>" for a in audits
        ) or '<p class="empty">Encara no hi ha activitat.</p>'
        modes = {
            "shadow": ("Observació", "Llegeix, processa i registra. No intenta executar cap acció."),
            "dry-run": ("Simulació", "Decideix què faria i ho registra, però no executa res."),
            "canary": ("Prova limitada", "Executa només per als remitents de prova autoritzats."),
            "live": ("Actiu", "Permet executar accions aprovades. Ara mateix l’execució continua sent local i simulada."),
        }
        options = "".join(
            f'<option value="{value}"{" selected" if value == mode else ""}>{_escape(label)}</option>'
            for value, (label, _) in modes.items()
        )
        mode_guide = "".join(
            f'<li class="mode-item{" current" if value == mode else ""}">'
            f'<span>{self._badge(value, label)}</span><p>{_escape(description)}</p></li>'
            for value, (label, description) in modes.items()
        )
        mode_label, mode_description = modes[mode]
        gmail_badge = self._badge("success" if gmail_state else "warning",
                                  "Sincronitzat" if gmail_state else "Pendent")
        gmail_time = self._time(gmail_state["updated_at"]) if gmail_state else "Encara sense cursor"
        service_states = {row["service"]: service_health(
            row, gmail_poll_seconds=self.settings.gmail_poll_seconds
        ) for row in service_rows}
        alerts = []
        service_labels = {"worker": "Worker", "gmail": "Gmail", "maintenance": "Manteniment"}
        for service in ("worker", "gmail", "maintenance"):
            state = service_states.get(service, "missing")
            if state != "healthy":
                alerts.append(f"{service_labels[service]}: {state}")
        backup_old = True
        if last_backup:
            backup_old = datetime.now(timezone.utc) - datetime.fromisoformat(last_backup["created_at"]) > timedelta(hours=26)
        if backup_old:
            alerts.append("No hi ha cap backup verificat de les últimes 26 hores")
        alert_html = "".join(f'<li>{_escape(item)}</li>' for item in alerts)
        alert_section = (f'<section class="section alert-card" role="status"><div><strong>Requereix atenció</strong>'
                         f'<ul>{alert_html}</ul></div><a href="/system">Veure sistema</a></section>') if alerts else ""
        executed = execution_counts.get("executed", 0)
        suppressed = execution_counts.get("suppressed", 0)
        content = f"""
<div class="app-shell">
  {self._header(csrf, "summary")}
  <main>
    <section class="hero">
      <p class="eyebrow">Centre d’operacions</p>
      <h1>Tot el que fa l’agent, en un sol lloc.</h1>
      <p class="hero-copy">Supervisa missatges, decisions de política, execucions simulades i errors sense perdre la traçabilitat.</p>
      <div class="status-row">{self._badge(mode, f'Mode: {mode_label}')}{gmail_badge}
        {self._badge('success', f'{executed} executades')}{self._badge('suppressed', f'{suppressed} suprimides')}</div>
    </section>
    {alert_section}
    <section class="section" aria-labelledby="overview-title">
      <div class="section-heading"><div><p class="eyebrow">Resum</p><h2 id="overview-title">Estat del sistema</h2></div><p>Actualitzat en carregar la pàgina</p></div>
      <div class="grid metrics">{metrics}</div>
    </section>
    <section class="section grid two" aria-label="Configuració operativa">
      <article class="card">
        <div class="card-header"><div><h2>Mode d’execució</h2><p>Controla fins on pot arribar una acció.</p></div>{self._badge(mode, mode_label)}</div>
        <form method="post" action="/admin/mode" class="mode-form">
          <input type="hidden" name="csrf" value="{_escape(csrf)}">
          <label>Mode actiu<select name="mode">{options}</select></label>
          <p class="mode-help"><strong>{_escape(mode_label)}:</strong> {_escape(mode_description)}</p>
          <button>Canviar mode</button>
        </form>
        <ul class="mode-guide" aria-label="Explicació dels modes d’execució">{mode_guide}</ul>
      </article>
      <article class="card">
        <div class="card-header"><div><h2>Connector Gmail</h2><p>Entrada oficial en només lectura.</p></div>{gmail_badge}</div>
        <div class="connector-state"><span class="connector-icon">G</span><span class="connector-meta"><strong>INBOX</strong><p>{gmail_time}</p></span></div>
        <p class="mode-help">Pot llegir i persistir correus. No pot enviar, eliminar, etiquetar ni marcar-los com a llegits.</p>
      </article>
    </section>
    <section class="section">
      <details class="card">
        <summary><span><strong>Simulador local</strong><br><span class="field-help">Crea un missatge de prova sense utilitzar cap canal extern.</span></span></summary>
        <div class="details-content">
          <form method="post" action="/simulate" class="simulator-form">
            <input type="hidden" name="csrf" value="{_escape(csrf)}">
            <div class="form-grid">
              <label>ID extern<input name="external_message_id" required maxlength="200" placeholder="prova-001"><span class="field-help">Ha de ser únic per evitar duplicats.</span></label>
              <label>Remitent<input name="sender" required maxlength="200" placeholder="usuari-prova"></label>
              <label>Destinatari<input name="recipient" required maxlength="200" placeholder="rrpp"></label>
              <label>Assumpte<input name="subject" maxlength="500" placeholder="Pregunta sobre l’esdeveniment"></label>
              <label class="full">Missatge<textarea name="body_text" required maxlength="20000" placeholder="Escriu el missatge de prova..."></textarea></label>
            </div>
            <button>Afegir a la cua</button>
          </form>
        </div>
      </details>
    </section>
    <section class="section card table-card" id="conversations">
      <div class="card-header"><div><h2>Converses recents</h2><p>Les 8 converses més recents de tots els canals.</p></div><a href="/conversations">Veure-les totes</a></div>
      <div class="table-scroll"><table><thead><tr><th>Conversa</th><th>Canal</th><th>Discoteca</th><th>Estat</th><th>Últim missatge</th></tr></thead><tbody>{conversation_rows}</tbody></table></div>
    </section>
    <section class="section card table-card" id="failures">
      <div class="card-header"><div><h2>Jobs fallits</h2><p>Errors terminals que necessiten una decisió humana.</p></div>{self._badge('danger' if failures else 'success', str(len(failures)))}</div>
      <div class="table-scroll"><table><thead><tr><th>ID</th><th>Intents</th><th>Error</th><th>Controls</th></tr></thead><tbody>{failure_rows}</tbody></table></div>
    </section>
    <section class="section card" id="activity">
      <div class="card-header"><div><h2>Activitat recent</h2><p>Només els últims 10 moviments; l’històric complet es conserva.</p></div><a href="/activity">Veure tot l’històric</a></div>
      <div class="activity-list">{audit_items}</div>
    </section>
  </main>
  <footer class="footer">RRPP Agent Bridge · infraestructura local segura i auditable</footer>
</div>"""
        return self._respond(start_response, "200 OK", self._page("RRPP Agent Bridge", content))

    def _dashboard(self, start_response, csrf: str):
        conn = self._connect()
        try:
            mode = get_mode(conn)
            counts = {
                "queued": conn.execute(
                    "SELECT count(DISTINCT e.conversation_id) FROM jobs j JOIN events e ON e.id=j.event_id "
                    "WHERE j.state='queued'"
                ).fetchone()[0],
                "processing": conn.execute(
                    "SELECT count(DISTINCT e.conversation_id) FROM jobs j JOIN events e ON e.id=j.event_id "
                    "WHERE j.state='processing'"
                ).fetchone()[0],
                "resolved": conn.execute(
                    "SELECT count(*) FROM conversations WHERE status='resolved'"
                ).fetchone()[0],
                "human": conn.execute(
                    "SELECT count(*) FROM conversations WHERE status='pending_review' OR bot_paused=1"
                ).fetchone()[0],
                "errors": conn.execute(
                    "SELECT (SELECT count(*) FROM jobs WHERE state='dead_letter') + "
                    "(SELECT count(*) FROM deliveries WHERE status IN ('failed','unknown'))"
                ).fetchone()[0],
            }
            conversations = conn.execute(
                "SELECT c.*,ra.external_account_id receiver_account,"
                "(SELECT body_text FROM conversation_messages m WHERE m.conversation_id=c.id "
                " ORDER BY m.created_at DESC,m.id DESC LIMIT 1) last_text,"
                "CASE WHEN c.bot_paused=1 OR c.status='pending_review' THEN 'pending_human' "
                "WHEN EXISTS(SELECT 1 FROM jobs j JOIN events e ON e.id=j.event_id "
                " WHERE e.conversation_id=c.id AND j.state='processing') THEN 'processing' "
                "WHEN EXISTS(SELECT 1 FROM jobs j JOIN events e ON e.id=j.event_id "
                " WHERE e.conversation_id=c.id AND j.state='queued') THEN 'queued' "
                "WHEN EXISTS(SELECT 1 FROM deliveries d WHERE d.conversation_id=c.id "
                " AND d.status IN ('failed','unknown')) THEN 'error' ELSE c.status END operational_status "
                "FROM conversations c LEFT JOIN receiver_accounts ra ON ra.id=c.receiver_account_id "
                "ORDER BY c.last_message_at DESC,c.id DESC LIMIT 12"
            ).fetchall()
            failures = conn.execute(
                "SELECT 'job' kind,id,last_error_code error,updated_at FROM jobs "
                "WHERE state='dead_letter' UNION ALL "
                "SELECT 'delivery',id,last_error_code,updated_at FROM deliveries "
                "WHERE status IN ('failed','unknown') ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()
            services = {row["service"]: row for row in conn.execute(
                "SELECT * FROM service_status WHERE service IN ('worker','maintenance')"
            )}
        finally:
            conn.close()
        labels = {
            "queued": "En cua", "processing": "Processant", "resolved": "Resoltes",
            "human": "Pendent d'una persona", "errors": "Errors",
        }
        metrics = "".join(
            f'<article class="card metric"><span class="metric-label">{labels[key]}</span>'
            f'<strong class="metric-value">{counts[key]}</strong>{self._badge(key)}</article>'
            for key in ("queued", "processing", "resolved", "human", "errors")
        )
        rows = "".join(
            f'<tr><td><a href="/conversations/{_escape(row["id"])}"><span class="cell-main">'
            f'<strong>{_escape(row["external_user_id"] or "Usuari desconegut")}</strong>'
            f'<small>{_escape((row["last_text"] or "")[:120])}</small></span></a></td>'
            f'<td>{self._badge(row["channel"])}</td><td>{_escape(row["receiver_account"] or "-")}</td>'
            f'<td>{self._badge(row["operational_status"])}</td><td>{self._time(row["last_message_at"])}</td></tr>'
            for row in conversations
        ) or '<tr><td colspan="5" class="empty">Encara no hi ha converses.</td></tr>'
        failure_rows = "".join(
            f'<tr><td>{self._badge(row["kind"])}</td><td>{self._short_id(row["id"])}</td>'
            f'<td>{_escape(row["error"] or "error_desconegut")}</td><td>{self._time(row["updated_at"])}</td></tr>'
            for row in failures
        ) or '<tr><td colspan="4" class="empty">No hi ha errors pendents.</td></tr>'
        service_cards = "".join(
            f'<article class="card service-card"><div class="card-header"><h2>{name.title()}</h2>'
            f'{self._badge(service_health(services.get(name)))}</div>'
            f'<p>{self._time(services[name]["heartbeat_at"] if name in services else None)}</p></article>'
            for name in ("worker", "maintenance")
        )
        mode_label = {"shadow": "Nomes lectura", "dry-run": "Nomes lectura",
                      "canary": "Prova limitada", "live": "Automatic"}.get(mode, mode)
        body = f'''<section class="hero"><p class="eyebrow">Centre d'operacions</p><h1>Converses i respostes en temps real.</h1><p class="hero-copy">OpenClaw prepara la decisio; el bridge valida, envia i registra cada resultat.</p><div class="status-row">{self._badge(mode, mode_label)}{self._badge("success" if self.settings.openclaw_enabled else "warning", "OpenClaw actiu" if self.settings.openclaw_enabled else "OpenClaw desactivat")}{self._badge("success" if self.settings.instagram_send_enabled else "warning", "Instagram envia" if self.settings.instagram_send_enabled else "Instagram nomes llegeix")}</div></section>
        <section class="section"><div class="grid metrics">{metrics}</div></section>
        <section class="section grid two"><article class="card"><div class="card-header"><div><h2>Mode operatiu</h2><p>Nomes s'envien respostes que superen la politica.</p></div>{self._badge(mode, mode_label)}</div><form method="post" action="/admin/mode" class="mode-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Comportament<select name="mode"><option value="shadow"{" selected" if mode in {"shadow", "dry-run"} else ""}>Nomes lectura</option><option value="live"{" selected" if mode == "live" else ""}>Automatic</option></select></label><button>Canviar mode</button></form></article><div class="grid">{service_cards}</div></section>
        <section class="section card table-card"><div class="card-header"><div><h2>Converses operatives</h2><p>Les 12 converses mes recents.</p></div><a href="/conversations">Veure-les totes</a></div><div class="table-scroll"><table><thead><tr><th>Usuari i ultim missatge</th><th>Canal</th><th>Compte</th><th>Estat</th><th>Actualitzada</th></tr></thead><tbody>{rows}</tbody></table></div></section>
        <section class="section card table-card"><div class="card-header"><div><h2>Errors que requereixen atencio</h2></div>{self._badge("danger" if failures else "success", str(len(failures)))}</div><div class="table-scroll"><table><thead><tr><th>Tipus</th><th>ID</th><th>Error</th><th>Data</th></tr></thead><tbody>{failure_rows}</tbody></table></div></section>'''
        return self._respond(
            start_response, "200 OK", self._layout("RRPP Agent Bridge", csrf, "summary", body)
        )

    def _conversations_legacy(self, start_response, csrf: str, query: dict[str, str]):
        status = query.get("status", "")
        channel = query.get("channel", "")
        venue = query.get("venue", "")
        search = query.get("q", "").strip()[:100]
        cursor = query.get("cursor", "")
        if status not in {"", "open", "pending_review", "resolved"}:
            raise ValueError("Invalid conversation status filter")
        if channel not in {"", "gmail", "instagram", "local"}:
            raise ValueError("Invalid channel filter")
        clauses, params = ["1=1"], []
        if status:
            clauses.append("c.status=?"); params.append(status)
        if channel:
            clauses.append("c.channel=?"); params.append(channel)
        if venue == "unassigned":
            clauses.append("c.venue_id IS NULL")
        elif venue:
            clauses.append("c.venue_id=?"); params.append(venue)
        if search:
            clauses.append("EXISTS(SELECT 1 FROM events se WHERE se.conversation_id=c.id AND (se.sender LIKE ? OR se.subject LIKE ? OR se.body_text LIKE ?))")
            term = f"%{search}%"; params.extend((term, term, term))
        if cursor:
            cursor_row = None
            conn = self._connect()
            try:
                cursor_row = conn.execute("SELECT last_message_at,id FROM conversations WHERE id=?", (cursor,)).fetchone()
            finally:
                conn.close()
            if not cursor_row:
                raise ValueError("Invalid conversation cursor")
            clauses.append("(c.last_message_at<? OR (c.last_message_at=? AND c.id<?))")
            params.extend((cursor_row["last_message_at"], cursor_row["last_message_at"], cursor_row["id"]))
        sql = (
            "SELECT c.*,v.name venue_name,(SELECT sender FROM events e WHERE e.conversation_id=c.id ORDER BY received_at DESC,id DESC LIMIT 1) sender,"
            "(SELECT subject FROM events e WHERE e.conversation_id=c.id ORDER BY received_at DESC,id DESC LIMIT 1) subject,"
            "(SELECT count(*) FROM events e WHERE e.conversation_id=c.id) message_count FROM conversations c "
            "LEFT JOIN venues v ON v.id=c.venue_id WHERE " + " AND ".join(clauses) +
            " ORDER BY c.last_message_at DESC,c.id DESC LIMIT 51"
        )
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            venues = conn.execute("SELECT id,name FROM venues WHERE active=1 ORDER BY name").fetchall()
        finally:
            conn.close()
        has_more, rows = len(rows) > 50, rows[:50]
        row_html = "".join(
            f'<tr><td><a href="/conversations/{_escape(row["id"])}"><span class="cell-main"><strong>{_escape(row["subject"] or "Sense assumpte")}</strong>'
            f'<small>{_escape(row["sender"])}</small></span></a></td><td>{self._badge(row["channel"])}</td>'
            f'<td>{_escape(row["venue_name"] or "Sense assignar")}</td><td>{self._badge(row["status"])}</td>'
            f'<td>{row["message_count"]}</td><td>{self._time(row["last_message_at"])}</td></tr>' for row in rows
        ) or '<tr><td colspan="6" class="empty">No hi ha converses amb aquests filtres.</td></tr>'
        venue_options = '<option value="">Totes</option><option value="unassigned">Sense assignar</option>' + "".join(
            f'<option value="{_escape(v["id"])}"{" selected" if venue == v["id"] else ""}>{_escape(v["name"])}</option>' for v in venues
        )
        filters = f"""<form method="get" class="filter-bar">
          <label>Cerca<input name="q" value="{_escape(search)}" placeholder="Remitent, assumpte o text"></label>
          <label>Discoteca<select name="venue">{venue_options}</select></label>
          <label>Canal<select name="channel"><option value="">Tots</option><option value="gmail"{" selected" if channel == "gmail" else ""}>Gmail</option><option value="instagram"{" selected" if channel == "instagram" else ""}>Instagram</option><option value="local"{" selected" if channel == "local" else ""}>Simulador</option></select></label>
          <label>Estat<select name="status"><option value="">Tots</option>{''.join(f'<option value="{s}"{" selected" if status == s else ""}>{s.replace("_", " ")}</option>' for s in ("open","pending_review","resolved"))}</select></label>
          <button>Filtrar</button></form>"""
        next_link = ""
        if has_more and rows:
            preserved = {k: v for k, v in query.items() if k != "cursor" and v}
            preserved["cursor"] = rows[-1]["id"]
            next_link = f'<a class="button secondary" href="/conversations?{urlencode(preserved)}">Següents converses</a>'
        body = f'<section class="hero compact"><p class="eyebrow">Operacions</p><h1>Converses</h1><p class="hero-copy">Missatges agrupats per fil, canal i discoteca.</p></section>{filters}'
        body += f'<section class="section card table-card"><div class="card-header"><div><h2>Converses</h2><p>Màxim 50 per pàgina.</p></div>{self._badge("info", str(len(rows)))}</div><div class="table-scroll"><table><thead><tr><th>Conversa</th><th>Canal</th><th>Discoteca</th><th>Estat</th><th>Missatges</th><th>Últim</th></tr></thead><tbody>{row_html}</tbody></table></div></section><div class="pagination">{next_link}</div>'
        return self._respond(start_response, "200 OK", self._layout("Converses · RRPP", csrf, "conversations", body))

    def _conversations(self, start_response, csrf: str, query: dict[str, str]):
        status = query.get("status", "")
        channel = query.get("channel", "")
        search = query.get("q", "").strip()[:100]
        if status not in {"", "queued", "processing", "resolved", "pending_human", "error"}:
            raise ValueError("Invalid conversation status filter")
        if channel not in {"", "instagram", "local"}:
            raise ValueError("Invalid channel filter")
        clauses, params = ["1=1"], []
        if channel:
            clauses.append("c.channel=?")
            params.append(channel)
        if search:
            clauses.append(
                "EXISTS(SELECT 1 FROM conversation_messages sm WHERE sm.conversation_id=c.id "
                "AND (sm.author_id LIKE ? OR sm.body_text LIKE ?))"
            )
            term = f"%{search}%"
            params.extend((term, term))
        status_sql = {
            "queued": "EXISTS(SELECT 1 FROM jobs j JOIN events e ON e.id=j.event_id WHERE e.conversation_id=c.id AND j.state='queued')",
            "processing": "EXISTS(SELECT 1 FROM jobs j JOIN events e ON e.id=j.event_id WHERE e.conversation_id=c.id AND j.state='processing')",
            "resolved": "c.status='resolved'",
            "pending_human": "(c.status='pending_review' OR c.bot_paused=1)",
            "error": "(EXISTS(SELECT 1 FROM jobs j JOIN events e ON e.id=j.event_id WHERE e.conversation_id=c.id AND j.state='dead_letter') OR EXISTS(SELECT 1 FROM deliveries d WHERE d.conversation_id=c.id AND d.status IN ('failed','unknown')))",
        }
        if status:
            clauses.append(status_sql[status])
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT c.*,ra.external_account_id receiver_account,"
                "(SELECT body_text FROM conversation_messages m WHERE m.conversation_id=c.id "
                " ORDER BY m.created_at DESC,m.id DESC LIMIT 1) last_text,"
                "(SELECT count(*) FROM conversation_messages m WHERE m.conversation_id=c.id) message_count,"
                "CASE WHEN c.bot_paused=1 OR c.status='pending_review' THEN 'pending_human' "
                "WHEN EXISTS(SELECT 1 FROM jobs j JOIN events e ON e.id=j.event_id WHERE e.conversation_id=c.id AND j.state='processing') THEN 'processing' "
                "WHEN EXISTS(SELECT 1 FROM jobs j JOIN events e ON e.id=j.event_id WHERE e.conversation_id=c.id AND j.state='queued') THEN 'queued' "
                "WHEN EXISTS(SELECT 1 FROM deliveries d WHERE d.conversation_id=c.id AND d.status IN ('failed','unknown')) THEN 'error' ELSE c.status END operational_status "
                "FROM conversations c LEFT JOIN receiver_accounts ra ON ra.id=c.receiver_account_id "
                "WHERE " + " AND ".join(clauses) + " ORDER BY c.last_message_at DESC,c.id DESC LIMIT 50",
                params,
            ).fetchall()
        finally:
            conn.close()
        row_html = "".join(
            f'<tr><td><a href="/conversations/{_escape(row["id"])}"><span class="cell-main">'
            f'<strong>{_escape(row["external_user_id"] or "Usuari desconegut")}</strong>'
            f'<small>{_escape((row["last_text"] or "")[:120])}</small></span></a></td>'
            f'<td>{self._badge(row["channel"])}</td><td>{_escape(row["receiver_account"] or "-")}</td>'
            f'<td>{self._badge(row["operational_status"])}</td><td>{row["message_count"]}</td>'
            f'<td>{self._time(row["last_message_at"])}</td></tr>' for row in rows
        ) or '<tr><td colspan="6" class="empty">No hi ha converses amb aquests filtres.</td></tr>'
        status_options = "".join(
            f'<option value="{value}"{" selected" if status == value else ""}>{label}</option>'
            for value, label in (("queued", "En cua"), ("processing", "Processant"),
                                 ("resolved", "Resoltes"), ("pending_human", "Pendent persona"),
                                 ("error", "Error"))
        )
        filters = f'''<form method="get" class="filter-bar"><label>Cerca<input name="q" value="{_escape(search)}" placeholder="Usuari o missatge"></label><label>Canal<select name="channel"><option value="">Tots</option><option value="instagram"{" selected" if channel == "instagram" else ""}>Instagram</option><option value="local"{" selected" if channel == "local" else ""}>Simulador</option></select></label><label>Estat<select name="status"><option value="">Tots</option>{status_options}</select></label><button>Filtrar</button></form>'''
        body = f'''<section class="hero compact"><p class="eyebrow">Operacions</p><h1>Converses</h1><p class="hero-copy">Una conversa per compte receptor i usuari extern.</p></section>{filters}<section class="section card table-card"><div class="table-scroll"><table><thead><tr><th>Usuari i ultim missatge</th><th>Canal</th><th>Compte</th><th>Estat</th><th>Missatges</th><th>Ultim</th></tr></thead><tbody>{row_html}</tbody></table></div></section>'''
        return self._respond(
            start_response, "200 OK", self._layout("Converses · RRPP", csrf, "conversations", body)
        )

    def _conversation_legacy(self, start_response, csrf: str, conversation_id: str):
        conn = self._connect()
        try:
            conversation = conn.execute("SELECT c.*,v.name venue_name FROM conversations c LEFT JOIN venues v ON v.id=c.venue_id WHERE c.id=?", (conversation_id,)).fetchone()
            if not conversation:
                return self._respond(start_response, "404 Not Found", self._page("No trobat", "Conversa desconeguda"))
            messages = conn.execute("SELECT * FROM events WHERE conversation_id=? ORDER BY received_at,id", (conversation_id,)).fetchall()
            reviews = conn.execute("SELECT r.*,a.type,p.reason FROM action_reviews r JOIN actions a ON a.id=r.action_id JOIN policy_decisions p ON p.action_id=a.id JOIN events e ON e.id=a.event_id WHERE e.conversation_id=? ORDER BY r.created_at DESC", (conversation_id,)).fetchall()
            venues = conn.execute("SELECT id,name FROM venues WHERE active=1 ORDER BY name").fetchall()
        finally:
            conn.close()
        messages_html = "".join(
            f'<article class="message-card"><div class="message-meta"><strong>{_escape(m["sender"])}</strong>{self._badge(m["channel"])}{self._time(m["received_at"])}</div><h3>{_escape(m["subject"] or "Sense assumpte")}</h3><p>{_escape(m["body_text"])}</p></article>' for m in messages
        )
        reviews_html = "".join(
            f'<article class="card"><div class="card-header"><div><h3>{_escape(r["type"].replace("_", " "))}</h3><p>{_escape(r["reason"])}</p></div>{self._badge(r["status"])}</div>{f"<p>{_escape(r["current_text"])}</p>" if r["current_text"] else ""}</article>' for r in reviews
        ) or '<p class="empty card">Aquesta conversa encara no té revisions.</p>'
        venue_options = '<option value="">Sense assignar</option>' + "".join(
            f'<option value="{_escape(v["id"])}"{" selected" if conversation["venue_id"] == v["id"] else ""}>{_escape(v["name"])}</option>' for v in venues
        )
        toggle = "reopen" if conversation["status"] == "resolved" else "resolve"
        toggle_label = "Reobrir conversa" if toggle == "reopen" else "Marcar com resolta"
        body = f'''<a class="back-link" href="/conversations">← Tornar a converses</a><section class="hero compact"><p class="eyebrow">{_escape(conversation["venue_name"] or "Sense assignar")}</p><h1>Conversa</h1><div class="status-row">{self._badge(conversation["channel"])}{self._badge(conversation["status"])}{self._badge("info", f"{len(messages)} missatges")}</div></section>
        <section class="grid two"><article class="card"><h2>Assignació</h2><form method="post" action="/conversations/{_escape(conversation_id)}/assign" class="mode-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Discoteca<select name="venue_id">{venue_options}</select></label><button>Desar assignació</button></form></article>
        <article class="card"><h2>Estat operatiu</h2><p class="mode-help">Els missatges nous reobren automàticament una conversa resolta.</p><form method="post" action="/conversations/{_escape(conversation_id)}/{toggle}"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button class="secondary">{toggle_label}</button></form></article></section>
        <section class="section"><div class="section-heading"><div><h2>Missatges</h2><p>Ordre cronològic del fil.</p></div></div><div class="message-list">{messages_html}</div></section>
        <section class="section"><div class="section-heading"><div><h2>Decisions i revisions</h2></div></div><div class="grid">{reviews_html}</div></section>'''
        return self._respond(start_response, "200 OK", self._layout("Conversa · RRPP", csrf, "conversations", body))

    def _conversation(self, start_response, csrf: str, conversation_id: str):
        conn = self._connect()
        try:
            conversation = conn.execute(
                "SELECT c.*,ra.external_account_id receiver_account FROM conversations c "
                "LEFT JOIN receiver_accounts ra ON ra.id=c.receiver_account_id WHERE c.id=?",
                (conversation_id,),
            ).fetchone()
            if not conversation:
                return self._respond(
                    start_response, "404 Not Found",
                    self._page("No trobat", "Conversa desconeguda"),
                )
            messages = conn.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY created_at,id",
                (conversation_id,),
            ).fetchall()
            reviews = conn.execute(
                "SELECT r.*,a.type,p.reason FROM action_reviews r "
                "JOIN actions a ON a.id=r.action_id "
                "JOIN policy_decisions p ON p.action_id=a.id "
                "JOIN events e ON e.id=a.event_id WHERE e.conversation_id=? "
                "ORDER BY r.created_at DESC", (conversation_id,),
            ).fetchall()
            jobs = conn.execute(
                "SELECT j.* FROM jobs j JOIN events e ON e.id=j.event_id "
                "WHERE e.conversation_id=? ORDER BY j.created_at DESC LIMIT 20",
                (conversation_id,),
            ).fetchall()
            deliveries = conn.execute(
                "SELECT * FROM deliveries WHERE conversation_id=? ORDER BY created_at DESC LIMIT 20",
                (conversation_id,),
            ).fetchall()
        finally:
            conn.close()
        messages_html = "".join(
            f'<article class="message-card {"outbound" if row["direction"] == "outbound" else "inbound"}">'
            f'<div class="message-meta"><strong>{_escape(row["author_type"])}</strong>'
            f'{self._badge(row["direction"])}{self._badge(row["status"])}'
            f'{self._time(row["created_at"])}</div><p>{_escape(row["body_text"])}</p></article>'
            for row in messages
        ) or '<p class="empty card">Encara no hi ha missatges.</p>'
        reviews_html = "".join(
            f'<article class="card"><div class="card-header"><div>'
            f'<h3>{_escape(row["type"].replace("_", " "))}</h3>'
            f'<p>{_escape(row["reason"])}</p></div>{self._badge(row["status"])}</div>'
            f'{f"<p>{_escape(row["current_text"])}</p>" if row["current_text"] else ""}</article>'
            for row in reviews
        ) or '<p class="empty card">Aquesta conversa no te revisions pendents.</p>'
        jobs_html = "".join(
            f'<tr><td><a href="/jobs/{_escape(row["id"])}">{self._short_id(row["id"])}</a></td>'
            f'<td>{self._badge(row["state"])}</td><td>{row["attempts"]}</td>'
            f'<td>{_escape(row["last_error_code"] or "-")}</td></tr>' for row in jobs
        ) or '<tr><td colspan="4" class="empty">Cap job.</td></tr>'
        deliveries_html = "".join(
            f'<tr><td>{self._short_id(row["id"])}</td><td>{self._badge(row["author_type"])}</td>'
            f'<td>{self._badge(row["status"])}</td><td>{_escape(row["last_error_code"] or "-")}</td>'
            f'<td>{self._time(row["sent_at"] or row["created_at"])}</td></tr>'
            for row in deliveries
        ) or '<tr><td colspan="5" class="empty">Cap enviament.</td></tr>'
        lifecycle = "reopen" if conversation["status"] == "resolved" else "resolve"
        lifecycle_label = "Reobrir conversa" if lifecycle == "reopen" else "Marcar resolta"
        bot_operation = "resume" if conversation["bot_paused"] else "pause"
        bot_label = "Retornar al bot" if conversation["bot_paused"] else "Pausar el bot"
        body = f'''<a class="back-link" href="/conversations">Tornar a converses</a>
        <section class="hero compact"><p class="eyebrow">{_escape(conversation["receiver_account"])} · {_escape(conversation["external_user_id"])}</p><h1>Conversa</h1><div class="status-row">{self._badge(conversation["channel"])}{self._badge(conversation["status"])}{self._badge("warning" if conversation["bot_paused"] else "success", "Bot pausat" if conversation["bot_paused"] else "Bot actiu")}{self._badge("info", f"{len(messages)} missatges")}</div></section>
        <section class="grid two"><article class="card"><h2>Resposta humana</h2><p class="mode-help">Utilitza la mateixa cua segura que el bot.</p><form method="post" action="/conversations/{_escape(conversation_id)}/reply" class="review-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Missatge<textarea name="text" maxlength="1000" required></textarea></label><button>Enviar per Instagram</button></form></article>
        <article class="card"><h2>Control del bot</h2><p class="mode-help">{_escape(conversation["pause_reason"] or "Els missatges nous reobren una conversa resolta.")}</p><form method="post" action="/conversations/{_escape(conversation_id)}/{bot_operation}" class="mode-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Motiu opcional<input name="reason" maxlength="500"></label><button class="secondary">{bot_label}</button></form><form method="post" action="/conversations/{_escape(conversation_id)}/{lifecycle}"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button class="secondary">{lifecycle_label}</button></form></article></section>
        <section class="section"><div class="section-heading"><div><h2>Historial complet</h2><p>Client, bot i equip en ordre cronologic.</p></div></div><div class="message-list">{messages_html}</div></section>
        <section class="section"><div class="section-heading"><div><h2>Decisions i revisions</h2></div></div><div class="grid">{reviews_html}</div></section>
        <section class="section grid two"><article class="card table-card"><div class="card-header"><h2>Jobs</h2></div><div class="table-scroll"><table><thead><tr><th>ID</th><th>Estat</th><th>Intents</th><th>Error</th></tr></thead><tbody>{jobs_html}</tbody></table></div></article><article class="card table-card"><div class="card-header"><h2>Enviaments</h2></div><div class="table-scroll"><table><thead><tr><th>ID</th><th>Autor</th><th>Estat</th><th>Error</th><th>Data</th></tr></thead><tbody>{deliveries_html}</tbody></table></div></article></section>'''
        return self._respond(
            start_response, "200 OK",
            self._layout("Conversa · RRPP", csrf, "conversations", body),
        )

    def _reviews(self, start_response, csrf: str, query: dict[str, str]):
        status = query.get("status", "pending")
        if status not in {"pending", "prepared", "rejected", "resolved", "all"}:
            raise ValueError("Invalid review status filter")
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT r.*,a.type,p.reason,e.conversation_id,c.channel,c.external_user_id,"
                "ra.external_account_id receiver_account FROM action_reviews r "
                "JOIN actions a ON a.id=r.action_id JOIN policy_decisions p ON p.action_id=a.id "
                "JOIN events e ON e.id=a.event_id JOIN conversations c ON c.id=e.conversation_id "
                "JOIN receiver_accounts ra ON ra.id=c.receiver_account_id "
                "WHERE (?='all' OR r.status=?) "
                "ORDER BY r.updated_at DESC,r.id DESC LIMIT 50", (status, status),
            ).fetchall()
        finally:
            conn.close()
        cards = []
        for row in rows:
            controls = ""
            if row["status"] == "pending" and row["kind"] == "draft":
                controls = f'''<form method="post" action="/reviews/{_escape(row["id"])}/edit" class="review-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Esborrany<textarea name="text" maxlength="1000" required>{_escape(row["current_text"])}</textarea></label><button>Desar canvis</button></form><div class="table-actions"><form method="post" action="/reviews/{_escape(row["id"])}/send"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button>Enviar per Instagram</button></form><form method="post" action="/reviews/{_escape(row["id"])}/reject"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button class="danger">Rebutjar</button></form></div>'''
            elif row["status"] == "pending":
                controls = f'<form method="post" action="/reviews/{_escape(row["id"])}/resolve"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button>Marcar escalació resolta</button></form>'
            cards.append(f'''<article class="card review-card"><div class="card-header"><div><p class="eyebrow">{_escape(row["receiver_account"])} · {_escape(row["external_user_id"])} · {_escape(row["channel"])}</p><h2>{_escape(row["type"].replace("_", " "))}</h2><p>{_escape(row["reason"])}</p></div>{self._badge(row["status"])}</div><a href="/conversations/{_escape(row["conversation_id"])}">Veure conversa</a>{controls}</article>''')
        tabs = " ".join(f'<a class="button {"" if status == s else "secondary"}" href="/reviews?status={s}">{label}</a>' for s, label in (("pending","Pendents"),("prepared","Preparats"),("rejected","Rebutjats"),("resolved","Resolts"),("all","Tots")))
        body = f'<section class="hero compact"><p class="eyebrow">Control humà</p><h1>Cua de revisió</h1><p class="hero-copy">Edita i envia una resposta quan el bot demani intervenció.</p></section><div class="tabs">{tabs}</div><section class="section grid">{"".join(cards) or "<p class=\"empty card\">No hi ha revisions en aquest estat.</p>"}</section>'
        return self._respond(start_response, "200 OK", self._layout("Revisió · RRPP", csrf, "reviews", body))

    def _activity(self, start_response, csrf: str, query: dict[str, str]):
        cursor_text = query.get("cursor", "")
        entity_type, outcome = query.get("entity_type", ""), query.get("outcome", "")
        venue = query.get("venue", "")
        if cursor_text and (not cursor_text.isdigit() or int(cursor_text) < 1):
            raise ValueError("Invalid activity cursor")
        if len(entity_type) > 40 or len(outcome) > 40:
            raise ValueError("Invalid activity filter")
        clauses, params = ["1=1"], []
        if cursor_text:
            clauses.append("a.id<?"); params.append(int(cursor_text))
        if entity_type:
            clauses.append("a.entity_type=?"); params.append(entity_type)
        if outcome:
            clauses.append("a.outcome=?"); params.append(outcome)
        if venue:
            clauses.append("((a.entity_type='conversation' AND a.entity_id IN (SELECT id FROM conversations WHERE venue_id=?)) OR (a.entity_type='event' AND a.entity_id IN (SELECT e.id FROM events e JOIN conversations c ON c.id=e.conversation_id WHERE c.venue_id=?)) OR (a.entity_type='job' AND a.entity_id IN (SELECT j.id FROM jobs j JOIN events e ON e.id=j.event_id JOIN conversations c ON c.id=e.conversation_id WHERE c.venue_id=?)) OR (a.entity_type='action' AND a.entity_id IN (SELECT ac.id FROM actions ac JOIN events e ON e.id=ac.event_id JOIN conversations c ON c.id=e.conversation_id WHERE c.venue_id=?)) OR (a.entity_type='review' AND a.entity_id IN (SELECT r.id FROM action_reviews r JOIN actions ac ON ac.id=r.action_id JOIN events e ON e.id=ac.event_id JOIN conversations c ON c.id=e.conversation_id WHERE c.venue_id=?)))")
            params.extend((venue,) * 5)
        conn = self._connect()
        try:
            rows = conn.execute("SELECT a.* FROM audit_log a WHERE " + " AND ".join(clauses) + " ORDER BY a.id DESC LIMIT 51", params).fetchall()
            venues = conn.execute("SELECT id,name FROM venues WHERE active=1 ORDER BY name").fetchall()
        finally:
            conn.close()
        has_more, rows = len(rows) > 50, rows[:50]
        items = "".join(f'<article class="activity-item"><span class="activity-dot"></span><span class="activity-copy"><strong>{_escape(r["operation"].replace(".", " · "))}</strong><small>{_escape(r["actor"])} · {_escape(r["entity_type"])} · {_escape(r["outcome"])}</small></span><span class="activity-time">{self._time(r["occurred_at"])}</span></article>' for r in rows) or '<p class="empty">No hi ha activitat amb aquests filtres.</p>'
        venue_options = '<option value="">Totes</option>' + "".join(f'<option value="{_escape(v["id"])}"{" selected" if venue == v["id"] else ""}>{_escape(v["name"])}</option>' for v in venues)
        filters = f'<form method="get" class="filter-bar"><label>Discoteca<select name="venue">{venue_options}</select></label><label>Tipus d’entitat<input name="entity_type" value="{_escape(entity_type)}" placeholder="conversation, review..."></label><label>Resultat<input name="outcome" value="{_escape(outcome)}" placeholder="pending, completed..."></label><button>Filtrar</button></form>'
        next_link = ""
        if has_more and rows:
            preserved = {k: v for k, v in query.items() if k != "cursor" and v}; preserved["cursor"] = str(rows[-1]["id"])
            next_link = f'<a class="button secondary" href="/activity?{urlencode(preserved)}">Activitat anterior</a>'
        body = f'<section class="hero compact"><p class="eyebrow">Traçabilitat</p><h1>Activitat</h1><p class="hero-copy">Històric complet, 50 registres per pàgina. No s’elimina activitat.</p></section>{filters}<section class="section card"><div class="activity-list">{items}</div></section><div class="pagination">{next_link}</div>'
        return self._respond(start_response, "200 OK", self._layout("Activitat · RRPP", csrf, "activity", body))

    def _venues(self, start_response, csrf: str):
        conn = self._connect()
        try:
            venues = conn.execute("SELECT v.*,(SELECT count(*) FROM conversations c WHERE c.venue_id=v.id) conversation_count FROM venues v ORDER BY v.active DESC,v.name").fetchall()
            catalog_events = conn.execute(
                "SELECT ce.*,v.name venue_name FROM catalog_events ce JOIN venues v ON v.id=ce.venue_id "
                "WHERE ce.active=1 ORDER BY ce.starts_at"
            ).fetchall()
            catalog_offers = conn.execute(
                "SELECT o.*,ce.name event_name FROM catalog_offers o "
                "JOIN catalog_events ce ON ce.id=o.event_id WHERE o.active=1 "
                "ORDER BY ce.starts_at,o.price_minor"
            ).fetchall()
        finally:
            conn.close()
        cards = []
        for venue in venues:
            cards.append(f'''<article class="card venue-card">
              <div class="card-header"><div><h2>{_escape(venue["name"])}</h2><p>{venue["conversation_count"]} converses · /{_escape(venue["slug"])}</p></div>{self._badge("success" if venue["active"] else "warning", "Activa" if venue["active"] else "Inactiva")}</div>
              <form method="post" action="/venues/{_escape(venue["id"])}/update" class="mode-form">
                <input type="hidden" name="csrf" value="{_escape(csrf)}">
                <label>Nom<input name="name" value="{_escape(venue["name"])}" required maxlength="120"></label>
                <label>Informació verificada per al bot<textarea name="bot_knowledge" maxlength="20000" rows="8" aria-describedby="knowledge-help-{_escape(venue["id"])}">{_escape(venue["bot_knowledge"])}</textarea><span id="knowledge-help-{_escape(venue["id"])}" class="field-help">Horaris, entrades, ubicació, normes i respostes confirmades. El bot contestarà en l’idioma i el to del client.</span></label>
                <label>Estat<select name="active"><option value="1"{" selected" if venue["active"] else ""}>Activa</option><option value="0"{" selected" if not venue["active"] else ""}>Inactiva</option></select></label>
                <button>Desar</button>
              </form>
            </article>''')
        body = f'''<section class="hero compact"><p class="eyebrow">Catàleg</p><h1>Discoteques</h1><p class="hero-copy">Informació comercial verificada que OpenClaw pot consultar des de qualsevol conversa.</p></section><section class="section grid two"><article class="card"><h2>Nova discoteca</h2><form method="post" action="/venues" class="mode-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Nom<input name="name" required maxlength="120" placeholder="Sala Nord"></label><label>Identificador intern (opcional)<input name="slug" maxlength="120" placeholder="sala-nord" aria-describedby="venue-slug-help"><span id="venue-slug-help" class="field-help">Es genera automàticament a partir del nom.</span></label><label>Informació verificada per al bot<textarea name="bot_knowledge" maxlength="20000" rows="8" placeholder="Horaris, ubicació, política d’entrades..."></textarea><span class="field-help">No incloguis secrets ni dades personals. La resposta seguirà l’idioma i el to del client.</span></label><button>Crear discoteca</button></form></article><article class="card"><h2>Com funciona</h2><p class="mode-help">Una conversa no pertany a una única discoteca. El bot rep només informació verificada del catàleg i pot comparar diverses opcions.</p></article></section><section class="section grid two">{"".join(cards) or "<p class=\"empty card\">Encara no hi ha discoteques.</p>"}</section>'''
        venue_options = "".join(
            f'<option value="{_escape(row["id"])}">{_escape(row["name"])}</option>'
            for row in venues if row["active"]
        )
        event_options = "".join(
            f'<option value="{_escape(row["id"])}">{_escape(row["venue_name"])} · {_escape(row["name"])}</option>'
            for row in catalog_events
        )
        catalog_rows = "".join(
            f'<tr><td>{_escape(row["venue_name"])}</td><td>{_escape(row["name"])}</td>'
            f'<td>{self._time(row["starts_at"])}</td><td>{self._badge(row["status"])}</td></tr>'
            for row in catalog_events
        ) or '<tr><td colspan="4" class="empty">Cap esdeveniment verificat.</td></tr>'
        def format_price(row):
            if row["price_minor"] is None:
                return "-"
            return f'{row["price_minor"] / 100:.2f} {row["currency"]}'

        offer_rows = "".join(
            f'<tr><td>{_escape(row["event_name"])}</td><td>{_escape(row["name"])}</td>'
            f'<td>{_escape(format_price(row))}</td>'
            f'<td>{self._badge(row["availability_status"])}</td></tr>' for row in catalog_offers
        ) or '<tr><td colspan="4" class="empty">Cap oferta verificada.</td></tr>'
        body += f'''<section class="section"><div class="section-heading"><div><p class="eyebrow">Coneixement comercial</p><h2>Esdeveniments i ofertes</h2></div></div><div class="grid two"><article class="card"><h3>Nou esdeveniment</h3><form method="post" action="/catalog/events" class="mode-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Discoteca<select name="venue_id" required>{venue_options}</select></label><label>Nom<input name="name" maxlength="160" required></label><label>Inici ISO amb zona horaria<input name="starts_at" placeholder="2026-07-18T23:00+02:00" required></label><label>Final opcional<input name="ends_at" placeholder="2026-07-19T06:00+02:00"></label><button>Crear esdeveniment verificat</button></form></article><article class="card"><h3>Nova oferta</h3><form method="post" action="/catalog/offers" class="mode-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Esdeveniment<select name="event_id" required>{event_options}</select></label><label>Nom<input name="name" maxlength="160" required></label><label>Tipus d'entrada<input name="ticket_type" maxlength="120" required></label><label>Preu EUR<input name="price" placeholder="15.00"></label><input type="hidden" name="currency" value="EUR"><label>Promocio<textarea name="promotion_text" maxlength="2000"></textarea></label><label>Condicions<textarea name="conditions" maxlength="4000"></textarea></label><label>Disponibilitat<select name="availability"><option value="available">Disponible</option><option value="unknown">No confirmada</option><option value="sold_out">Exhaurida</option></select></label><label>Enllac HTTPS<input name="link" maxlength="2000"></label><button>Crear oferta verificada</button></form></article></div><article class="card table-card"><h3>Esdeveniments actius</h3><div class="table-scroll"><table><thead><tr><th>Discoteca</th><th>Esdeveniment</th><th>Inici</th><th>Estat</th></tr></thead><tbody>{catalog_rows}</tbody></table></div><h3>Ofertes actives</h3><div class="table-scroll"><table><thead><tr><th>Esdeveniment</th><th>Oferta</th><th>Preu</th><th>Disponibilitat</th></tr></thead><tbody>{offer_rows}</tbody></table></div></article></section>'''
        return self._respond(start_response, "200 OK", self._layout("Discoteques · RRPP", csrf, "venues", body))

    def _system(self, start_response, csrf: str):
        conn = self._connect()
        try:
            services = conn.execute("SELECT * FROM service_status ORDER BY service").fetchall()
            backups = conn.execute("SELECT * FROM backup_records ORDER BY created_at DESC LIMIT 20").fetchall()
            dead_letters = conn.execute("SELECT count(*) FROM jobs WHERE state='dead_letter'").fetchone()[0]
            pending_reviews = conn.execute("SELECT count(*) FROM action_reviews WHERE status='pending'").fetchone()[0]
            mode, schema = get_mode(conn), current_version(conn)
        finally:
            conn.close()
        service_map = {row["service"]: row for row in services}
        labels = {"worker": "Worker", "maintenance": "Manteniment"}
        service_cards = []
        for name in ("worker", "maintenance"):
            row = service_map.get(name)
            state = service_health(row)
            service_cards.append(f'''<article class="card service-card"><div class="card-header"><div><h2>{labels[name]}</h2><p>{_escape(row["instance_id"] if row else "Encara no iniciat")}</p></div>{self._badge(state)}</div><dl class="compact-record"><dt>Heartbeat</dt><dd>{self._time(row["heartbeat_at"] if row else None)}</dd><dt>Últim èxit</dt><dd>{self._time(row["last_success_at"] if row else None)}</dd><dt>Últim error</dt><dd>{_escape(row["last_error_code"] if row else "—")}</dd></dl></article>''')
        backup_rows = "".join(
            f'<tr><td>{_escape(row["kind"])}</td><td>{_escape(row["filename"])}</td><td>{row["size_bytes"]}</td>'
            f'<td>{self._badge(row["integrity_status"])}</td><td>{self._badge("success" if row["encrypted_export"] else "warning", "Sí" if row["encrypted_export"] else "No")}</td>'
            f'<td>{self._time(row["created_at"])}</td></tr>' for row in backups
        ) or '<tr><td colspan="6" class="empty">Encara no hi ha backups registrats.</td></tr>'
        body = f'''<section class="hero compact"><p class="eyebrow">Operacions</p><h1>Sistema</h1><p class="hero-copy">Salut dels processos, recuperació i estat persistent.</p><div class="status-row">{self._badge(mode, f"Mode: {mode}")}{self._badge("info", f"Esquema {schema}/{latest_version()}")}{self._badge("danger" if dead_letters else "success", f"{dead_letters} fallits")}{self._badge("warning" if pending_reviews else "success", f"{pending_reviews} revisions")}</div></section><section class="section grid service-grid">{"".join(service_cards)}</section><section class="section card table-card"><div class="card-header"><div><h2>Backups recents</h2><p>Últims 20 registres verificats o fallits.</p></div>{self._badge("info", str(len(backups)))}</div><div class="table-scroll"><table><thead><tr><th>Tipus</th><th>Fitxer</th><th>Bytes</th><th>Integritat</th><th>Xifrat</th><th>Creat</th></tr></thead><tbody>{backup_rows}</tbody></table></div></section><section class="section card"><h2>Accés privat</h2><p class="mode-help">En VPS, el dashboard només escolta a localhost. Accedeix-hi amb un túnel SSH; no exposis el port 8080 públicament.</p></section>'''
        return self._respond(start_response, "200 OK", self._layout("Sistema · RRPP", csrf, "system", body))

    def _detail(self, start_response, kind: str, entity_id: str):
        conn = self._connect()
        try:
            table = {"events": "events", "jobs": "jobs", "actions": "actions"}[kind]
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
            if row is None:
                return self._respond(start_response, "404 Not Found", self._page("Not found", "Unknown entity"))
            related = []
            entity_ids = [entity_id]
            if kind == "events":
                jobs = conn.execute("SELECT * FROM jobs WHERE event_id=?", (entity_id,)).fetchall()
                actions = conn.execute("SELECT * FROM actions WHERE event_id=?", (entity_id,)).fetchall()
                executions = conn.execute(
                    "SELECT x.* FROM action_executions x JOIN actions a ON a.id=x.action_id "
                    "WHERE a.event_id=?", (entity_id,),
                ).fetchall()
                related = [*jobs, *actions, *executions]
                entity_ids.extend([item["id"] for item in jobs])
                entity_ids.extend([item["id"] for item in actions])
            elif kind == "jobs":
                actions = conn.execute("SELECT * FROM actions WHERE job_id=?", (entity_id,)).fetchall()
                executions = conn.execute(
                    "SELECT x.* FROM action_executions x JOIN actions a ON a.id=x.action_id "
                    "WHERE a.job_id=?", (entity_id,),
                ).fetchall()
                related = [*actions, *executions]
                entity_ids.extend([item["id"] for item in actions])
            else:
                related = conn.execute(
                    "SELECT * FROM action_executions WHERE action_id=?", (entity_id,)
                ).fetchall()
            placeholders = ",".join("?" for _ in entity_ids)
            audits = conn.execute(
                f"SELECT * FROM audit_log WHERE entity_id IN ({placeholders}) ORDER BY id", entity_ids
            ).fetchall()
        finally:
            conn.close()
        def render(item):
            return '<dl class="record-grid">' + "".join(
                f"<dt>{_escape(key.replace('_', ' '))}</dt><dd>{_escape(item[key]) or '—'}</dd>"
                for key in item.keys()
            ) + "</dl>"
        kind_label = {"events": "Esdeveniment", "jobs": "Job", "actions": "Acció"}[kind]
        related_html = "".join(f'<article class="card">{render(item)}</article>' for item in related)
        audit_html = "".join(f'<article class="card">{render(item)}</article>' for item in audits)
        content = f"""
<div class="app-shell">
  <header class="topbar">{self._brand()}<div class="topbar-actions">{self._badge(kind)}</div></header>
  <main>
    <a class="back-link" href="/">← Tornar al dashboard</a>
    <section class="hero"><p class="eyebrow">Traçabilitat</p><h1>{kind_label}</h1>
      <p class="hero-copy">Identificador complet: <span class="id-link">{_escape(entity_id)}</span></p></section>
    <section class="section"><div class="section-heading"><div><h2>Dades principals</h2><p>Informació persistent de l’entitat.</p></div></div><article class="card">{render(row)}</article></section>
    <section class="section"><div class="section-heading"><div><h2>Registres relacionats</h2><p>Jobs, accions o execucions connectades.</p></div>{self._badge('info', str(len(related)))}</div><div class="grid">{related_html or '<p class="empty card">No hi ha registres relacionats.</p>'}</div></section>
    <section class="section"><div class="section-heading"><div><h2>Auditoria</h2><p>Decisions i transicions correlacionades.</p></div>{self._badge('info', str(len(audits)))}</div><div class="grid">{audit_html or '<p class="empty card">No hi ha entrades d’auditoria.</p>'}</div></section>
  </main>
  <footer class="footer">RRPP Agent Bridge · detall operatiu</footer>
</div>"""
        return self._respond(start_response, "200 OK", self._page(f"{kind_label} · RRPP", content))

    @staticmethod
    def _page(title: str, content: str) -> str:
        return f"""<!doctype html>
<html lang="ca">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#090b10">
  <title>{_escape(title)}</title>
  <link rel="stylesheet" href="/assets/dashboard.css">
</head>
<body>{content}</body>
</html>"""
