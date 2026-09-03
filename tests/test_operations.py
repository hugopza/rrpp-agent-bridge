from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from rrpp_bridge.config import Settings
from rrpp_bridge.db import backup_database, connect, current_version, initialize, latest_version
from rrpp_bridge.operations import (
    apply_retention,
    create_backup,
    restore_backup,
    retry_pending_exports,
    run_maintenance,
    service_health,
    start_service,
    stop_service,
    verify_backup,
)
from rrpp_bridge.runtime_lock import DatabaseRuntimeLock


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = self.root / "bridge.db"
        self.backups = self.root / "backups"
        self.exports = self.root / "exports"
        self.conn = connect(self.database)
        initialize(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_service_health_distinguishes_healthy_error_stale_and_stopped(self):
        now = datetime.now(timezone.utc)
        start_service(self.conn, "worker", "worker:test")
        row = self.conn.execute("SELECT * FROM service_status WHERE service='worker'").fetchone()
        self.assertEqual("healthy", service_health(row, now=now))
        self.conn.execute("UPDATE service_status SET last_error_at=?,last_error_code='RuntimeError' WHERE service='worker'",
                          (now.isoformat(),))
        row = self.conn.execute("SELECT * FROM service_status WHERE service='worker'").fetchone()
        self.assertEqual("error", service_health(row, now=now))
        self.conn.execute("UPDATE service_status SET heartbeat_at=? WHERE service='worker'",
                          ((now - timedelta(seconds=31)).isoformat(),))
        row = self.conn.execute("SELECT * FROM service_status WHERE service='worker'").fetchone()
        self.assertEqual("stale", service_health(row, now=now))
        stop_service(self.conn, "worker", "worker:test")
        row = self.conn.execute("SELECT * FROM service_status WHERE service='worker'").fetchone()
        self.assertEqual("stopped", service_health(row, now=now))

    def test_backup_captures_wal_is_verified_and_detects_corruption(self):
        self.conn.execute("INSERT INTO audit_log VALUES(NULL,?,?,?,?,?,?,?)",
                          ("now", "test", "wal", "database", "db", "ok", "{}"))
        info = create_backup(self.database, self.backups)
        checked = verify_backup(info.path)
        copied = sqlite3.connect(info.path)
        try:
            self.assertEqual(1, copied.execute("SELECT count(*) FROM audit_log WHERE operation='wal'").fetchone()[0])
        finally:
            copied.close()
        self.assertEqual(info.sha256, checked.sha256)
        info.path.write_bytes(b"not a sqlite database")
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            verify_backup(info.path)

    def test_age_export_uses_public_recipient_without_identity(self):
        def fake_age(command, **kwargs):
            target = Path(command[command.index("--output") + 1])
            target.write_bytes(b"encrypted")
            return None
        with patch("rrpp_bridge.operations.shutil.which", return_value="/usr/bin/age"), \
                patch("rrpp_bridge.operations.subprocess.run", side_effect=fake_age) as run:
            info = create_backup(self.database, self.backups, export_dir=self.exports,
                                 age_recipient="age1publictest")
        self.assertTrue(info.encrypted_path.is_file())
        command = run.call_args.args[0]
        self.assertIn("age1publictest", command)
        self.assertNotIn("--identity", command)

    def test_failed_encrypted_export_is_retried_without_creating_another_backup(self):
        with patch("rrpp_bridge.operations.shutil.which", return_value="/usr/bin/age"), \
                patch("rrpp_bridge.operations.subprocess.run", side_effect=OSError("age failed")):
            with self.assertRaisesRegex(RuntimeError, "encrypted export failed"):
                create_backup(
                    self.database, self.backups, "daily", export_dir=self.exports,
                    age_recipient="age1publictest",
                )
        self.assertEqual(("verified", 0), tuple(self.conn.execute(
            "SELECT integrity_status,encrypted_export FROM backup_records"
        ).fetchone()))

        def fake_age(command, **_kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(b"encrypted")

        with patch("rrpp_bridge.operations.shutil.which", return_value="/usr/bin/age"), \
                patch("rrpp_bridge.operations.subprocess.run", side_effect=fake_age):
            self.assertEqual(1, retry_pending_exports(
                self.conn, self.backups, self.exports, "age1publictest"
            ))
        self.assertEqual(1, self.conn.execute(
            "SELECT encrypted_export FROM backup_records"
        ).fetchone()[0])
        self.assertEqual(1, len(list(self.backups.glob("*.db"))))

    def test_restore_requires_confirmation_rejects_active_services_and_rolls_data_back(self):
        self.conn.execute("INSERT INTO audit_log VALUES(NULL,?,?,?,?,?,?,?)",
                          ("before", "test", "before", "database", "db", "ok", "{}"))
        info = create_backup(self.database, self.backups)
        self.conn.execute("INSERT INTO audit_log VALUES(NULL,?,?,?,?,?,?,?)",
                          ("after", "test", "after", "database", "db", "ok", "{}"))
        with self.assertRaises(PermissionError):
            restore_backup(self.database, info.path, self.backups, confirmation="NO")
        start_service(self.conn, "worker", "worker:test")
        with self.assertRaisesRegex(RuntimeError, "Stop active services"):
            restore_backup(self.database, info.path, self.backups, confirmation="RESTORE")
        stop_service(self.conn, "worker", "worker:test")
        self.conn.close()
        safety = restore_backup(self.database, info.path, self.backups, confirmation="RESTORE")
        self.assertTrue(safety.is_file())
        self.conn = connect(self.database)
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM audit_log WHERE operation='after'").fetchone()[0])
        self.assertEqual("ok", self.conn.execute("PRAGMA integrity_check").fetchone()[0])

    def test_restore_accepts_supported_older_schema_and_migrates_it(self):
        self.conn.execute("DELETE FROM schema_migrations WHERE version=?", (latest_version(),))
        old_backup = backup_database(self.database)
        self.assertIsNotNone(old_backup)
        initialize(self.conn)
        self.conn.close()
        restore_backup(self.database, old_backup, self.backups, confirmation="RESTORE")
        self.conn = connect(self.database)
        self.assertEqual(latest_version(), current_version(self.conn))

    @unittest.skipUnless(os.name == "posix", "flock is the production Linux safeguard")
    def test_restore_lock_rejects_active_bridge_processes(self):
        shared = DatabaseRuntimeLock(self.database, exclusive=False)
        try:
            with self.assertRaisesRegex(RuntimeError, "Stop all bridge processes"):
                DatabaseRuntimeLock(self.database, exclusive=True, blocking=False)
        finally:
            shared.close()

    def test_retention_keeps_seven_daily_and_three_monthly_verified_backups(self):
        for kind, count in (("daily", 9), ("monthly", 5)):
            for index in range(count):
                name = f"{kind}-{index}.db"
                (self.backups / name).parent.mkdir(parents=True, exist_ok=True)
                (self.backups / name).write_bytes(b"backup")
                self.conn.execute(
                    "INSERT INTO backup_records VALUES(?,?,?,?,?,?,'verified',0,?,NULL)",
                    (f"{kind}-{index}", f"2026-{index + 1:02d}-01T00:00:00+00:00", kind,
                     name, 6, "hash", f"2026-{index + 1:02d}-01T00:00:00+00:00"),
                )
        removed = apply_retention(self.database, self.backups)
        self.assertEqual(4, len(removed))
        self.assertEqual(7, self.conn.execute("SELECT count(*) FROM backup_records WHERE kind='daily'").fetchone()[0])
        self.assertEqual(3, self.conn.execute("SELECT count(*) FROM backup_records WHERE kind='monthly'").fetchone()[0])

    def test_maintenance_once_creates_verified_backup_and_stops_cleanly(self):
        settings = Settings(
            self.database, "shadow", "admin", "long-test-password", "s" * 32,
            backup_dir=self.backups, backup_export_dir=self.exports,
            backup_hour=3, backup_timezone="Europe/Madrid",
        )
        run_maintenance(settings, once=True)
        row = self.conn.execute("SELECT integrity_status,kind FROM backup_records").fetchone()
        service = self.conn.execute("SELECT stopped_at,last_success_at FROM service_status WHERE service='maintenance'").fetchone()
        self.assertEqual(("verified", "monthly"), tuple(row))
        self.assertIsNotNone(service["stopped_at"])
        self.assertIsNotNone(service["last_success_at"])

    def test_pending_export_failure_does_not_block_a_due_local_backup(self):
        settings = Settings(
            self.database,
            "shadow",
            "admin",
            "long-test-password",
            "s" * 32,
            backup_dir=self.backups,
            backup_export_dir=self.exports,
            backup_hour=3,
            backup_timezone="Europe/Madrid",
        )
        with patch(
            "rrpp_bridge.operations.retry_pending_exports",
            side_effect=RuntimeError("off-host unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Pending encrypted backup export failed"):
                run_maintenance(settings, once=True)
        row = self.conn.execute(
            "SELECT integrity_status,kind FROM backup_records"
        ).fetchone()
        self.assertEqual(("verified", "monthly"), tuple(row))


if __name__ == "__main__":
    unittest.main()
