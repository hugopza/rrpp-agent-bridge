from __future__ import annotations

import hashlib
import hmac
import io
import json
import uuid
from urllib.parse import parse_qs

from .adapters.instagram import normalize
from .audit import record, utc_now
from .config import Settings
from .db import connect, prepare_runtime, transaction
from .queue import JobQueue
from .runtime import initialize_mode

MAX_BODY_BYTES = 262_144


class InstagramWebhookApplication:
    def __init__(self, settings: Settings):
        self.settings = settings
        conn = connect(settings.database_path)
        try:
            prepare_runtime(conn)
            initialize_mode(conn, settings.mode)
        finally:
            conn.close()

    @staticmethod
    def _respond(start_response, status: str, body: str = "", content_type: str = "text/plain; charset=utf-8"):
        data = body.encode("utf-8")
        start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(data))),
                                ("Cache-Control", "no-store")])
        return [data]

    def _verify_get(self, environ: dict, start_response):
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        mode = query.get("hub.mode", [""])[0]
        token = query.get("hub.verify_token", [""])[0]
        challenge = query.get("hub.challenge", [""])[0]
        if mode != "subscribe" or not hmac.compare_digest(token, self.settings.instagram_verify_token):
            return self._respond(start_response, "403 Forbidden", "Forbidden")
        return self._respond(start_response, "200 OK", challenge)

    def _read_body(self, environ: dict) -> bytes:
        if environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().casefold() != "application/json":
            raise ValueError("Unsupported content type")
        try:
            length = int(environ.get("CONTENT_LENGTH") or -1)
        except ValueError as exc:
            raise ValueError("Invalid content length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("Invalid request size")
        body = environ.get("wsgi.input", io.BytesIO()).read(length)
        if len(body) != length:
            raise ValueError("Incomplete request body")
        return body

    def _verify_signature(self, environ: dict, body: bytes) -> bool:
        supplied = environ.get("HTTP_X_HUB_SIGNATURE_256", "")
        if not supplied.startswith("sha256=") or len(supplied) != 71:
            return False
        expected = hmac.new(self.settings.instagram_app_secret.encode("utf-8"), body,
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied[7:].casefold(), expected)

    def _post(self, environ: dict, start_response):
        try:
            body = self._read_body(environ)
        except ValueError:
            return self._respond(start_response, "400 Bad Request", "Invalid request")
        if not self._verify_signature(environ, body):
            return self._respond(start_response, "403 Forbidden", "Forbidden")
        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Payload must be an object")
            events, sanitized, ignored = normalize(payload, self.settings.instagram_business_account_id)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return self._respond(start_response, "400 Bad Request", "Invalid request")
        digest = hashlib.sha256(body).hexdigest()
        conn = connect(self.settings.database_path)
        try:
            with transaction(conn, immediate=True):
                prior = conn.execute(
                    "SELECT id FROM inbound_webhook_receipts WHERE provider='instagram' AND payload_sha256=?",
                    (digest,),
                ).fetchone()
                if prior is not None:
                    record(conn, "adapter.instagram", "webhook.duplicate", "webhook_receipt",
                           prior["id"], "ignored")
                    return self._respond(start_response, "200 OK", "EVENT_RECEIVED")
                accepted = duplicates = 0
                queue = JobQueue(conn)
                for event in events:
                    _, created = queue.enqueue_in_transaction(event)
                    accepted += int(created)
                    duplicates += int(not created)
                receipt_id = f"wh_{uuid.uuid4().hex}"
                status = "accepted" if accepted else "ignored"
                conn.execute(
                    "INSERT INTO inbound_webhook_receipts VALUES(?,?,?,?,?,?,?,?,?)",
                    (receipt_id, "instagram", digest,
                     json.dumps(sanitized, separators=(",", ":")), status,
                     accepted, duplicates, ignored, utc_now()),
                )
                record(conn, "adapter.instagram", "webhook.received", "webhook_receipt",
                       receipt_id, status, {"accepted": accepted, "duplicates": duplicates,
                                            "ignored": ignored})
        except Exception:
            return self._respond(start_response, "500 Internal Server Error", "Temporary failure")
        finally:
            conn.close()
        return self._respond(start_response, "200 OK", "EVENT_RECEIVED")

    def __call__(self, environ, start_response):
        if not self.settings.instagram_enabled:
            return self._respond(start_response, "404 Not Found", "Not found")
        if environ.get("PATH_INFO", "") != "/webhooks/instagram":
            return self._respond(start_response, "404 Not Found", "Not found")
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if method == "GET":
            return self._verify_get(environ, start_response)
        if method == "POST":
            return self._post(environ, start_response)
        return self._respond(start_response, "405 Method Not Allowed", "Method not allowed")
