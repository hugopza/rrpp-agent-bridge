from __future__ import annotations

import hashlib
import hmac
import html
import re
import secrets
import time
from http import cookies
from importlib import resources
from urllib.parse import parse_qs
from wsgiref.util import setup_testing_defaults

from .config import Settings
from .db import connect, initialize
from .queue import JobQueue
from .runtime import get_mode, initialize_mode, set_mode
from .service import ingest_local


def _escape(value: object) -> str:
    return html.escape(str(value or ""))


class Application:
    def __init__(self, settings: Settings):
        self.settings = settings
        conn = self._connect()
        initialize(conn)
        initialize_mode(conn, settings.mode)
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
    def _brand() -> str:
        return """<a class="brand" href="/" aria-label="RRPP Agent Bridge, inici">
  <span class="brand-mark" aria-hidden="true">RB</span>
  <span class="brand-copy"><strong>RRPP Agent Bridge</strong><span>Operacions i auditoria</span></span>
</a>"""

    @staticmethod
    def _badge(value: object, label: str | None = None) -> str:
        text = str(value or "unknown")
        css = re.sub(r"[^a-z0-9_-]", "-", text.casefold())
        return f'<span class="badge {css}">{_escape(label or text.replace("_", " "))}</span>'

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
            counts = {row["state"]: row["n"] for row in conn.execute("SELECT state,count(*) n FROM jobs GROUP BY state")}
            execution_counts = {row["status"]: row["n"] for row in conn.execute("SELECT status,count(*) n FROM action_executions GROUP BY status")}
            events = conn.execute("SELECT * FROM events ORDER BY ingested_at DESC LIMIT 20").fetchall()
            actions = conn.execute("SELECT a.*,p.outcome,p.policy_id FROM actions a JOIN policy_decisions p ON p.action_id=a.id ORDER BY a.created_at DESC LIMIT 20").fetchall()
            audits = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 30").fetchall()
            failures = conn.execute("SELECT * FROM jobs WHERE state='dead_letter' ORDER BY updated_at DESC LIMIT 20").fetchall()
            gmail_state = conn.execute(
                "SELECT updated_at FROM connector_state WHERE connector='gmail' AND key='history_id'"
            ).fetchone()
        finally:
            conn.close()
        metric_labels = {
            "queued": "En cua", "processing": "Processant", "completed": "Completats",
            "dead_letter": "Fallits", "dismissed": "Descartats",
        }
        metrics = "".join(
            f'<article class="card metric"><span class="metric-label">{label}</span>'
            f'<strong class="metric-value">{counts.get(key, 0)}</strong>{self._badge(key)}</article>'
            for key, label in metric_labels.items()
        )
        event_rows = "".join(
            f"<tr><td><a class='id-link' href='/events/{_escape(e['id'])}'>{self._short_id(e['id'])}</a></td>"
            f"<td>{self._badge(e['channel'])}</td><td><span class='cell-main'><strong>{_escape(e['sender'])}</strong>"
            f"<small>{_escape(e['subject'] or 'Sense assumpte')}</small></span></td>"
            f"<td>{self._badge(e['status'])}</td><td>{self._time(e['ingested_at'])}</td></tr>" for e in events
        ) or '<tr><td class="empty" colspan="5">Encara no hi ha esdeveniments.</td></tr>'
        action_rows = "".join(
            f"<tr><td><a class='id-link' href='/actions/{_escape(a['id'])}'>{self._short_id(a['id'])}</a></td>"
            f"<td><span class='cell-main'><strong>{_escape(a['type'].replace('_', ' '))}</strong>"
            f"<small>{_escape(a['policy_id'])}</small></span></td><td>{self._badge(a['outcome'])}</td>"
            f"<td>{self._badge(a['state'])}</td></tr>" for a in actions
        ) or '<tr><td class="empty" colspan="4">Encara no s’han generat accions.</td></tr>'
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
        executed = execution_counts.get("executed", 0)
        suppressed = execution_counts.get("suppressed", 0)
        content = f"""
<div class="app-shell">
  <header class="topbar">
    {self._brand()}
    <div class="topbar-actions">{self._badge(mode, mode_label)}
      <form method="post" action="/logout" class="inline-form">
        <input type="hidden" name="csrf" value="{_escape(csrf)}">
        <button class="secondary">Tancar sessió</button>
      </form>
    </div>
  </header>
  <main>
    <section class="hero">
      <p class="eyebrow">Centre d’operacions</p>
      <h1>Tot el que fa l’agent, en un sol lloc.</h1>
      <p class="hero-copy">Supervisa missatges, decisions de política, execucions simulades i errors sense perdre la traçabilitat.</p>
      <div class="status-row">{self._badge(mode, f'Mode: {mode_label}')}{gmail_badge}
        {self._badge('success', f'{executed} executades')}{self._badge('suppressed', f'{suppressed} suprimides')}</div>
    </section>
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
    <section class="section card table-card" id="events">
      <div class="card-header"><div><h2>Missatges rebuts</h2><p>Últims 20 esdeveniments de tots els canals.</p></div>{self._badge('info', str(len(events)))}</div>
      <div class="table-scroll"><table><thead><tr><th>ID</th><th>Canal</th><th>Missatge</th><th>Estat</th><th>Ingerit</th></tr></thead><tbody>{event_rows}</tbody></table></div>
    </section>
    <section class="section card table-card" id="actions">
      <div class="card-header"><div><h2>Accions i política</h2><p>Què ha proposat el sistema i quina decisió s’ha aplicat.</p></div>{self._badge('info', str(len(actions)))}</div>
      <div class="table-scroll"><table><thead><tr><th>ID</th><th>Acció</th><th>Decisió</th><th>Execució</th></tr></thead><tbody>{action_rows}</tbody></table></div>
    </section>
    <section class="section card table-card" id="failures">
      <div class="card-header"><div><h2>Jobs fallits</h2><p>Errors terminals que necessiten una decisió humana.</p></div>{self._badge('danger' if failures else 'success', str(len(failures)))}</div>
      <div class="table-scroll"><table><thead><tr><th>ID</th><th>Intents</th><th>Error</th><th>Controls</th></tr></thead><tbody>{failure_rows}</tbody></table></div>
    </section>
    <section class="section card" id="activity">
      <div class="card-header"><div><h2>Activitat recent</h2><p>Traça cronològica dels últims moviments.</p></div>{self._badge('info', str(len(audits)))}</div>
      <div class="activity-list">{audit_items}</div>
    </section>
  </main>
  <footer class="footer">RRPP Agent Bridge · infraestructura local segura i auditable</footer>
</div>"""
        return self._respond(start_response, "200 OK", self._page("RRPP Agent Bridge", content))

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
