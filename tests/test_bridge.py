from __future__ import annotations

import io
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from rrpp_bridge.config import Settings, load_local_env
from rrpp_bridge.db import backup_database, connect, current_version, initialize, latest_version
from rrpp_bridge.executor import Executor
from rrpp_bridge.queue import JobQueue
from rrpp_bridge.runtime import get_mode, initialize_mode, set_mode
from rrpp_bridge.service import ingest_local, process_one
from rrpp_bridge.web import Application


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "bridge.db"
        self.conn = connect(self.path)
        initialize(self.conn)
        initialize_mode(self.conn, "shadow")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    @staticmethod
    def payload(message_id="m-1", body="When is the event?", sender="test-user"):
        return {"external_message_id": message_id, "sender": sender,
                "recipient": "promoter", "subject": "Question", "body_text": body}

    def test_ingestion_is_durable_and_idempotent(self):
        first, created = ingest_local(self.conn, self.payload())
        second, duplicate_created = ingest_local(self.conn, self.payload())
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first, second)
        self.assertEqual(1, self.conn.execute("SELECT count(*) FROM events").fetchone()[0])
        self.assertEqual("queued", self.conn.execute("SELECT state FROM jobs").fetchone()[0])

    def test_shadow_worker_suppresses_and_audits_draft(self):
        ingest_local(self.conn, self.payload())
        self.assertTrue(process_one(self.conn))
        action = self.conn.execute("SELECT * FROM actions").fetchone()
        decision = self.conn.execute("SELECT * FROM policy_decisions").fetchone()
        execution = self.conn.execute("SELECT * FROM action_executions").fetchone()
        self.assertEqual(("draft_reply", "suppressed", "shadow"),
                         (action["type"], action["state"], action["mode"]))
        self.assertEqual("allowed", decision["outcome"])
        self.assertEqual(("suppressed", "mode_shadow", 1),
                         (execution["status"], execution["reason"], execution["simulated"]))

    def test_sensitive_request_is_escalated_and_never_executed(self):
        set_mode(self.conn, "live", "test")
        ingest_local(self.conn, self.payload(body="Confirma una reserva VIP i el pagament"))
        process_one(self.conn)
        self.assertEqual("escalate_to_owner", self.conn.execute("SELECT type FROM actions").fetchone()[0])
        self.assertEqual("escalated", self.conn.execute("SELECT outcome FROM policy_decisions").fetchone()[0])
        execution = self.conn.execute("SELECT status,reason FROM action_executions").fetchone()
        self.assertEqual(("suppressed", "policy_escalated"), tuple(execution))

    def test_mode_execution_matrix(self):
        cases = [
            ("shadow", "user-a", frozenset(), "suppressed", "mode_shadow"),
            ("dry-run", "user-b", frozenset(), "suppressed", "mode_dry_run"),
            ("canary", "user-c", frozenset({"user-c"}), "executed", "local_simulated_sink"),
            ("canary", "user-d", frozenset({"other"}), "suppressed", "canary_sender_not_allowed"),
            ("live", "user-e", frozenset(), "executed", "local_simulated_sink"),
        ]
        for index, (mode, sender, allowlist, status, reason) in enumerate(cases):
            set_mode(self.conn, mode, "test")
            ingest_local(self.conn, self.payload(f"matrix-{index}", sender=sender))
            Executor(self.conn, canary_senders=allowlist).run_once()
            row = self.conn.execute(
                "SELECT x.status,x.reason,x.simulated FROM action_executions x "
                "JOIN actions a ON a.id=x.action_id JOIN events e ON e.id=a.event_id "
                "WHERE e.external_message_id=?", (f"matrix-{index}",)
            ).fetchone()
            self.assertEqual((status, reason, 1), tuple(row))

    def test_backoff_then_dead_letter(self):
        ingest_local(self.conn, self.payload())
        queue = JobQueue(self.conn)
        job = queue.claim_next("test-worker")
        before = datetime.now(timezone.utc)
        queue.fail(job, RuntimeError("controlled"), "test-worker", 2)
        retry = self.conn.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual("queued", retry["state"])
        self.assertGreater(datetime.fromisoformat(retry["available_at"]), before)
        self.conn.execute("UPDATE jobs SET available_at='2000-01-01T00:00:00.000+00:00'")
        job = queue.claim_next("test-worker")
        queue.fail(job, RuntimeError("controlled"), "test-worker", 2)
        self.assertEqual("dead_letter", self.conn.execute("SELECT state FROM jobs").fetchone()[0])

    def test_expired_lease_is_recovered(self):
        ingest_local(self.conn, self.payload())
        queue = JobQueue(self.conn)
        job = queue.claim_next("crashed-worker")
        self.conn.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (job["id"],))
        self.assertEqual(1, queue.recover_stale(3))
        recovered = self.conn.execute("SELECT state,worker_id FROM jobs").fetchone()
        self.assertEqual(("queued", None), tuple(recovered))

    def test_work_key_serializes_same_conversation(self):
        ingest_local(self.conn, self.payload("same-1"))
        ingest_local(self.conn, self.payload("same-2"))
        ingest_local(self.conn, self.payload("other", sender="other-user"))
        queue = JobQueue(self.conn)
        first = queue.claim_next("worker-1")
        second = queue.claim_next("worker-2")
        self.assertNotEqual(first["work_key"], second["work_key"])
        self.assertEqual(1, self.conn.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0])

    def test_manual_retry_and_dismiss_are_audited(self):
        for message_id in ("retry", "dismiss"):
            ingest_local(self.conn, self.payload(message_id))
            job = JobQueue(self.conn).claim_next("worker")
            JobQueue(self.conn).fail(job, RuntimeError("controlled"), "worker", 1)
        ids = [self.conn.execute(
            "SELECT j.id FROM jobs j JOIN events e ON e.id=j.event_id WHERE e.external_message_id=?",
            (message_id,),
        ).fetchone()[0] for message_id in ("retry", "dismiss")]
        queue = JobQueue(self.conn)
        self.assertTrue(queue.retry(ids[0], "admin"))
        self.assertTrue(queue.dismiss(ids[1], "admin"))
        states = [self.conn.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                  for job_id in ids]
        self.assertEqual(["queued", "dismissed"], states)
        self.assertEqual(2, self.conn.execute(
            "SELECT count(*) FROM audit_log WHERE operation IN ('job.retried','job.dismissed')"
        ).fetchone()[0])

    def test_oversized_payload_is_not_persisted(self):
        with self.assertRaises(ValueError):
            ingest_local(self.conn, self.payload(body="x" * 20_001))
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM events").fetchone()[0])


class MigrationTests(unittest.TestCase):
    def test_version_one_database_upgrades_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "legacy.db")
            try:
                sql = resources.files("rrpp_bridge.sql").joinpath("001_initial.sql").read_text(encoding="utf-8")
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations VALUES(1,datetime('now'))")
                conn.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                             ("evt_old", "local", "old", "a", "b", "", "body", "now", "now", "{}", "local:a:b", "queued"))
                self.assertEqual(list(range(2, latest_version() + 1)), initialize(conn))
                self.assertEqual(latest_version(), current_version(conn))
                self.assertEqual("evt_old", conn.execute("SELECT id FROM events").fetchone()[0])
            finally:
                conn.close()

    def test_sqlite_backup_contains_wal_commits_and_initialize_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.db"
            conn = connect(path)
            try:
                self.assertEqual(list(range(1, latest_version() + 1)), initialize(conn))
                initialize_mode(conn, "shadow")
                conn.execute("INSERT INTO audit_log VALUES(NULL,?,?,?,?,?,?,?)",
                             ("now", "test", "backup", "database", "db", "ok", "{}"))
                backup = backup_database(path)
                self.assertEqual([], initialize(conn))
                copied = connect(backup)
                try:
                    self.assertEqual(1, copied.execute("SELECT count(*) FROM audit_log").fetchone()[0])
                finally:
                    copied.close()
            finally:
                conn.close()


class ConfigTests(unittest.TestCase):
    def test_local_env_loads_only_rrpp_keys_without_overriding_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("RRPP_MODE=live\nRRPP_PORT=9090\n", encoding="utf-8")
            with patch.dict("os.environ", {"RRPP_MODE": "shadow"}, clear=True):
                load_local_env(path)
                self.assertEqual("shadow", os.environ["RRPP_MODE"])
                self.assertEqual("9090", os.environ["RRPP_PORT"])

    def test_local_env_rejects_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("UNSAFE_KEY=value\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_local_env(path)

class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "web.db"
        settings = Settings(self.path, "shadow", "admin", "long-test-password", "s" * 32)
        self.app = Application(settings)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, path="/", method="GET", body="", cookie=""):
        captured = {}
        def start_response(status, headers):
            captured.update(status=status, headers=dict(headers))
        raw = body.encode()
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "SERVER_NAME": "test",
                   "SERVER_PORT": "80", "SERVER_PROTOCOL": "HTTP/1.1", "wsgi.url_scheme": "http",
                   "wsgi.input": io.BytesIO(raw), "CONTENT_LENGTH": str(len(raw)),
                   "CONTENT_TYPE": "application/x-www-form-urlencoded", "HTTP_COOKIE": cookie}
        response = b"".join(self.app(environ, start_response)).decode()
        return captured, response

    def login(self):
        result, _ = self.request("/login", "POST", "username=admin&password=long-test-password")
        cookie = result["headers"]["Set-Cookie"].split(";", 1)[0]
        csrf = cookie.split("=", 1)[1].split(".")[2]
        return cookie, csrf

    def test_dashboard_is_private_and_login_cookie_is_hardened(self):
        anonymous, _ = self.request()
        self.assertEqual("303 See Other", anonymous["status"])
        cookie, _ = self.login()
        self.assertIn("rrpp_session=", cookie)

    def test_mode_change_requires_csrf_and_is_audited(self):
        cookie, csrf = self.login()
        denied, _ = self.request("/admin/mode", "POST", "mode=live", cookie)
        self.assertEqual("403 Forbidden", denied["status"])
        changed, _ = self.request("/admin/mode", "POST", f"mode=live&csrf={csrf}", cookie)
        self.assertEqual("303 See Other", changed["status"])
        conn = connect(self.path)
        self.assertEqual("live", get_mode(conn))
        self.assertEqual(1, conn.execute("SELECT count(*) FROM audit_log WHERE operation='mode.changed'").fetchone()[0])
        conn.close()

    def test_expired_session_is_rejected(self):
        raw = f"admin.{int(time.time()) - 1}.expired-csrf"
        cookie = f"rrpp_session={self.app._sign(raw)}"
        result, _ = self.request(cookie=cookie)
        self.assertEqual("303 See Other", result["status"])
        self.assertEqual("/login", result["headers"]["Location"])

    def test_dashboard_can_retry_and_dismiss_dead_letter_jobs(self):
        conn = connect(self.path)
        ids = []
        for message_id in ("web-retry", "web-dismiss"):
            ingest_local(conn, BridgeTests.payload(message_id))
            job = JobQueue(conn).claim_next("failed-worker")
            JobQueue(conn).fail(job, RuntimeError("controlled"), "failed-worker", 1)
            ids.append(job["id"])
        conn.close()
        cookie, csrf = self.login()
        retried, _ = self.request(f"/admin/jobs/{ids[0]}/retry", "POST", f"csrf={csrf}", cookie)
        dismissed, _ = self.request(f"/admin/jobs/{ids[1]}/dismiss", "POST", f"csrf={csrf}", cookie)
        self.assertEqual(("303 See Other", "303 See Other"),
                         (retried["status"], dismissed["status"]))
        conn = connect(self.path)
        states = [conn.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                  for job_id in ids]
        conn.close()
        self.assertEqual(["queued", "dismissed"], states)

    def test_end_to_end_web_ingestion_and_independent_worker_connection(self):
        cookie, csrf = self.login()
        body = f"csrf={csrf}&external_message_id=e2e&sender=test-user&recipient=promoter&subject=Hi&body_text=Hello"
        accepted, _ = self.request("/simulate", "POST", body, cookie)
        self.assertEqual("303 See Other", accepted["status"])
        worker_conn = connect(self.path)
        Executor(worker_conn).run_once("independent-worker")
        self.assertEqual("completed", worker_conn.execute("SELECT state FROM jobs").fetchone()[0])
        event_id = worker_conn.execute("SELECT id FROM events").fetchone()[0]
        worker_conn.close()
        detail, page = self.request(f"/events/{event_id}", cookie=cookie)
        self.assertEqual("200 OK", detail["status"])
        self.assertIn("independent-worker", page)


if __name__ == "__main__":
    unittest.main()
