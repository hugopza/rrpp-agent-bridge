from __future__ import annotations

import hashlib
import hmac
import html
import re
import secrets
import time
from http import cookies
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
    def _respond(start_response, status: str, body: str, headers=None):
        encoded = body.encode("utf-8")
        base = [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(encoded))),
                ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                ("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'"),
                ("Cache-Control", "no-store")]
        start_response(status, base + (headers or []))
        return [encoded]

    def _csrf_form(self, environ: dict, csrf: str) -> dict[str, str]:
        form = self._body(environ)
        if not hmac.compare_digest(form.get("csrf", ""), csrf):
            raise PermissionError("Invalid CSRF token")
        return form

    def __call__(self, environ, start_response):
        setup_testing_defaults(environ)
        path, method = environ["PATH_INFO"], environ["REQUEST_METHOD"]
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
                error = "Invalid credentials"
            except ValueError as exc:
                error = str(exc)
        form = f"""<form method=post><label>User <input name=username required autocomplete=username></label>
<label>Password <input name=password type=password required autocomplete=current-password></label>
<button type=submit>Sign in</button></form><p class=error>{_escape(error)}</p>"""
        return self._respond(start_response, "200 OK", self._page("Private dashboard", form))

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
        finally:
            conn.close()
        metrics = " ".join(f"<strong>{_escape(k)}</strong>: {_escape(counts.get(k, 0))}" for k in ("queued", "processing", "completed", "dead_letter", "dismissed"))
        event_rows = "".join(f"<tr><td><a href=/events/{_escape(e['id'])}>{_escape(e['id'])}</a></td><td>{_escape(e['sender'])}</td><td>{_escape(e['subject'])}</td><td>{_escape(e['status'])}</td><td>{_escape(e['ingested_at'])}</td></tr>" for e in events) or "<tr><td colspan=5>No events</td></tr>"
        action_rows = "".join(f"<tr><td><a href=/actions/{_escape(a['id'])}>{_escape(a['id'])}</a></td><td>{_escape(a['type'])}</td><td>{_escape(a['outcome'])}</td><td>{_escape(a['state'])}</td><td>{_escape(a['policy_id'])}</td></tr>" for a in actions) or "<tr><td colspan=5>No actions</td></tr>"
        failure_rows = "".join(f"<tr><td><a href=/jobs/{_escape(j['id'])}>{_escape(j['id'])}</a></td><td>{_escape(j['attempts'])}</td><td>{_escape(j['last_error_code'])}: {_escape(j['last_error_message'])}</td><td><form method=post action=/admin/jobs/{_escape(j['id'])}/retry><input type=hidden name=csrf value='{_escape(csrf)}'><button>Retry</button></form><form method=post action=/admin/jobs/{_escape(j['id'])}/dismiss><input type=hidden name=csrf value='{_escape(csrf)}'><button>Dismiss</button></form></td></tr>" for j in failures) or "<tr><td colspan=4>No failures</td></tr>"
        audit_rows = "".join(f"<tr><td>{_escape(a['occurred_at'])}</td><td>{_escape(a['actor'])}</td><td>{_escape(a['operation'])}</td><td>{_escape(a['outcome'])}</td></tr>" for a in audits) or "<tr><td colspan=4>No activity</td></tr>"
        options = "".join(f"<option value={value}{' selected' if value == mode else ''}>{value}</option>" for value in ("shadow", "dry-run", "canary", "live"))
        content = f"""<header><h1>RRPP Agent Bridge</h1><p>Mode: <strong>{_escape(mode)}</strong> | {metrics} | executions: {_escape(execution_counts)}</p>
<form method=post action=/admin/mode><input type=hidden name=csrf value="{_escape(csrf)}"><label>Execution mode <select name=mode>{options}</select></label><button>Change mode</button></form>
<form method=post action=/logout><input type=hidden name=csrf value="{_escape(csrf)}"><button>Sign out</button></form></header>
<section><h2>Local simulator</h2><form method=post action=/simulate><input type=hidden name=csrf value="{_escape(csrf)}"><label>External ID <input name=external_message_id required maxlength=200></label><label>Sender <input name=sender required maxlength=200></label><label>Recipient <input name=recipient required maxlength=200></label><label>Subject <input name=subject maxlength=500></label><label>Message <textarea name=body_text required maxlength=20000></textarea></label><button>Persist event</button></form></section>
<section><h2>Events</h2><table><tr><th>ID</th><th>Sender</th><th>Subject</th><th>Status</th><th>Ingested</th></tr>{event_rows}</table></section>
<section><h2>Actions and policy</h2><table><tr><th>ID</th><th>Type</th><th>Decision</th><th>State</th><th>Policy</th></tr>{action_rows}</table></section>
<section><h2>Failed jobs</h2><table><tr><th>ID</th><th>Attempts</th><th>Error</th><th>Controls</th></tr>{failure_rows}</table></section>
<section><h2>Recent activity</h2><table><tr><th>Time</th><th>Actor</th><th>Operation</th><th>Outcome</th></tr>{audit_rows}</table></section>"""
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
            return "<dl>" + "".join(f"<dt>{_escape(key)}</dt><dd>{_escape(item[key])}</dd>" for key in item.keys()) + "</dl>"
        content = f"<p><a href='/'>Dashboard</a></p><h1>{_escape(kind)} detail</h1>{render(row)}"
        content += "<h2>Related</h2>" + ("".join(render(item) for item in related) or "<p>None</p>")
        content += "<h2>Audit</h2>" + ("".join(render(item) for item in audits) or "<p>None</p>")
        return self._respond(start_response, "200 OK", self._page(f"{kind} detail", content))

    @staticmethod
    def _page(title: str, content: str) -> str:
        return f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width"><title>{_escape(title)}</title>
<style>body{{font:15px system-ui;max-width:1200px;margin:auto;padding:24px;background:#f5f6f8;color:#17202a}}section,header{{background:white;padding:18px;margin:14px 0;border-radius:8px}}form{{display:grid;gap:10px;max-width:650px}}label{{display:grid;gap:4px}}input,textarea,select,button{{font:inherit;padding:8px}}textarea{{min-height:90px}}table{{border-collapse:collapse;width:100%;display:block;overflow:auto}}th,td{{padding:8px;border-bottom:1px solid #ddd;text-align:left;white-space:nowrap}}dl{{display:grid;grid-template-columns:180px 1fr}}dt,dd{{padding:5px;margin:0;border-bottom:1px solid #ddd;overflow-wrap:anywhere}}.error{{color:#a00}}</style><body>{content}</body></html>"""
