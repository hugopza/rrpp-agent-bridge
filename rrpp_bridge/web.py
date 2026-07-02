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
from .db import connect, current_version, latest_version, prepare_runtime
from .queue import JobQueue
from .runtime import get_mode, initialize_mode, set_mode
from .operations import service_health
from .service import ingest_local
from .workspace import (add_route, assign_conversation, create_venue, disable_route, edit_review,
                        set_conversation_status, transition_review, update_venue)


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
        body = body.replace(
            '<option value="gmail">Gmail</option><option value="local">Simulador</option>',
            '<option value="gmail">Gmail</option><option value="instagram">Instagram</option>'
            '<option value="local">Simulador</option>',
        )
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
        channel_labels = {"gmail": "Gmail", "instagram": "Instagram", "local": "Simulador"}
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
                    event_id, created = ingest_local(conn, form)
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
            match = re.fullmatch(r"/conversations/([A-Za-z0-9_-]+)/(assign|resolve|reopen)", path)
            if match and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    actor = f"dashboard:{self.settings.dashboard_user}"
                    if match.group(2) == "assign":
                        venue_id = form.get("venue_id", "") or None
                        changed = assign_conversation(conn, match.group(1), venue_id, actor)
                    else:
                        status = "resolved" if match.group(2) == "resolve" else "open"
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
            match = re.fullmatch(r"/reviews/([A-Za-z0-9_-]+)/(edit|approve|reject|resolve)", path)
            if match and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    actor = f"dashboard:{self.settings.dashboard_user}"
                    operation = match.group(2)
                    changed = edit_review(conn, match.group(1), form.get("text", ""), actor) if operation == "edit" else transition_review(
                        conn, match.group(1), {"approve": "prepared", "reject": "rejected", "resolve": "resolved"}[operation], actor
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
            if path == "/venues" and method == "POST":
                form = self._csrf_form(environ, csrf)
                conn = self._connect()
                try:
                    create_venue(conn, form.get("name", ""), form.get("slug", ""),
                                 form.get("language", ""), f"dashboard:{self.settings.dashboard_user}")
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
                                               form.get("language", ""), form.get("active") == "1", actor)
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

    def _dashboard(self, start_response, csrf: str):
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

    def _conversations(self, start_response, csrf: str, query: dict[str, str]):
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

    def _conversation(self, start_response, csrf: str, conversation_id: str):
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

    def _reviews(self, start_response, csrf: str, query: dict[str, str]):
        status = query.get("status", "pending")
        if status not in {"pending", "prepared", "rejected", "resolved", "all"}:
            raise ValueError("Invalid review status filter")
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT r.*,a.type,p.reason,e.conversation_id,c.channel,v.name venue_name FROM action_reviews r "
                "JOIN actions a ON a.id=r.action_id JOIN policy_decisions p ON p.action_id=a.id "
                "JOIN events e ON e.id=a.event_id JOIN conversations c ON c.id=e.conversation_id "
                "LEFT JOIN venues v ON v.id=c.venue_id WHERE (?='all' OR r.status=?) "
                "ORDER BY r.updated_at DESC,r.id DESC LIMIT 50", (status, status),
            ).fetchall()
        finally:
            conn.close()
        cards = []
        for row in rows:
            controls = ""
            if row["status"] == "pending" and row["kind"] == "draft":
                controls = f'''<form method="post" action="/reviews/{_escape(row["id"])}/edit" class="review-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Esborrany<textarea name="text" maxlength="20000" required>{_escape(row["current_text"])}</textarea></label><button>Desar canvis</button></form><div class="table-actions"><form method="post" action="/reviews/{_escape(row["id"])}/approve"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button>Aprovar com preparat</button></form><form method="post" action="/reviews/{_escape(row["id"])}/reject"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button class="danger">Rebutjar</button></form></div>'''
            elif row["status"] == "pending":
                controls = f'<form method="post" action="/reviews/{_escape(row["id"])}/resolve"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button>Marcar escalació resolta</button></form>'
            cards.append(f'''<article class="card review-card"><div class="card-header"><div><p class="eyebrow">{_escape(row["venue_name"] or "Sense assignar")} · {_escape(row["channel"])}</p><h2>{_escape(row["type"].replace("_", " "))}</h2><p>{_escape(row["reason"])}</p></div>{self._badge(row["status"])}</div><a href="/conversations/{_escape(row["conversation_id"])}">Veure conversa</a>{controls}</article>''')
        tabs = " ".join(f'<a class="button {"" if status == s else "secondary"}" href="/reviews?status={s}">{label}</a>' for s, label in (("pending","Pendents"),("prepared","Preparats"),("rejected","Rebutjats"),("resolved","Resolts"),("all","Tots")))
        body = f'<section class="hero compact"><p class="eyebrow">Control humà</p><h1>Cua de revisió</h1><p class="hero-copy">Aprovar mai envia: només deixa un esborrany preparat.</p></section><div class="tabs">{tabs}</div><section class="section grid">{"".join(cards) or "<p class=\"empty card\">No hi ha revisions en aquest estat.</p>"}</section>'
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
            routes = conn.execute("SELECT * FROM venue_routes ORDER BY channel,recipient").fetchall()
        finally:
            conn.close()
        route_map = {}
        for route in routes:
            route_map.setdefault(route["venue_id"], []).append(route)
        def render_route(route):
            control = ""
            if route["active"]:
                control = (
                    f'<form method="post" action="/routes/{_escape(route["id"])}/disable">'
                    f'<input type="hidden" name="csrf" value="{_escape(csrf)}">'
                    '<button class="secondary">Desactivar</button></form>'
                )
            state = self._badge("success" if route["active"] else "warning",
                                "Activa" if route["active"] else "Inactiva")
            return (f'<li><span>{self._badge(route["channel"])} '
                    f'{_escape(route["recipient"])} {state}</span>{control}</li>')
        cards = []
        for venue in venues:
            route_list = "".join(render_route(route) for route in route_map.get(venue["id"], []))
            route_list = route_list or '<li class="field-help">Cap regla configurada</li>'
            cards.append(f'''<article class="card venue-card"><div class="card-header"><div><h2>{_escape(venue["name"])}</h2><p>{venue["conversation_count"]} converses · /{_escape(venue["slug"])}</p></div>{self._badge("success" if venue["active"] else "warning", "Activa" if venue["active"] else "Inactiva")}</div><form method="post" action="/venues/{_escape(venue["id"])}/update" class="mode-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Nom<input name="name" value="{_escape(venue["name"])}" required maxlength="120"></label><label>Idioma<select name="language"><option value="ca"{" selected" if venue["default_language"] == "ca" else ""}>Català</option><option value="es"{" selected" if venue["default_language"] == "es" else ""}>Castellà</option></select></label><label>Estat<select name="active"><option value="1"{" selected" if venue["active"] else ""}>Activa</option><option value="0"{" selected" if not venue["active"] else ""}>Inactiva</option></select></label><button>Desar</button></form><h3>Regles d’assignació</h3><ul class="route-list">{route_list}</ul><form method="post" action="/venues/{_escape(venue["id"])}/routes" class="form-grid"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Canal<select name="channel"><option value="gmail">Gmail</option><option value="local">Simulador</option></select></label><label>Destinatari o alias<input name="recipient" required maxlength="500"></label><button>Afegir regla</button></form></article>''')
        body = f'''<section class="hero compact"><p class="eyebrow">Organització</p><h1>Discoteques</h1><p class="hero-copy">Configuració operativa i routing exacte per canal i destinatari.</p></section><section class="section grid two"><article class="card"><h2>Nova discoteca</h2><form method="post" action="/venues" class="mode-form"><input type="hidden" name="csrf" value="{_escape(csrf)}"><label>Nom<input name="name" required maxlength="120"></label><label>Identificador<input name="slug" required pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="nom-discoteca"></label><label>Idioma<select name="language"><option value="ca">Català</option><option value="es">Castellà</option></select></label><button>Crear discoteca</button></form></article><article class="card"><h2>Com funciona</h2><p class="mode-help">Les regles comparen el destinatari exacte. Si no coincideix, la conversa queda Sense assignar. El contingut del missatge mai decideix la discoteca.</p></article></section><section class="section grid two">{"".join(cards) or "<p class=\"empty card\">Encara no hi ha discoteques.</p>"}</section>'''
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
        labels = {"worker": "Worker", "gmail": "Gmail", "maintenance": "Manteniment"}
        service_cards = []
        for name in ("worker", "gmail", "maintenance"):
            row = service_map.get(name)
            state = service_health(row, gmail_poll_seconds=self.settings.gmail_poll_seconds)
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
