from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .audit import record, utc_now
from .db import connect, current_version, latest_version, transaction

SERVICES = frozenset({"worker", "gmail", "maintenance"})
BACKUP_KINDS = frozenset({"manual", "daily", "monthly", "pre_restore"})


def instance_id(service: str) -> str:
    return f"{service}:{socket.gethostname()}:{os.getpid()}"


def start_service(conn: sqlite3.Connection, service: str, instance: str) -> None:
    if service not in SERVICES:
        raise ValueError("Unknown service")
    timestamp = utc_now()
    with transaction(conn, immediate=True):
        conn.execute(
            "INSERT INTO service_status(service,instance_id,started_at,heartbeat_at,stopped_at,details_json) "
            "VALUES(?,?,?,?,NULL,'{}') ON CONFLICT(service) DO UPDATE SET "
            "instance_id=excluded.instance_id,started_at=excluded.started_at,"
            "heartbeat_at=excluded.heartbeat_at,stopped_at=NULL,last_error_at=NULL,last_error_code=NULL",
            (service, instance, timestamp, timestamp),
        )
        record(conn, f"service.{service}", "service.started", "service", service, "running",
               {"instance_id": instance})


def heartbeat(conn: sqlite3.Connection, service: str, instance: str, *,
              success: bool = False, error: Exception | None = None,
              details: dict[str, object] | None = None) -> None:
    timestamp = utc_now()
    safe_details = json.dumps(details or {}, separators=(",", ":"))
    if error:
        conn.execute(
            "UPDATE service_status SET heartbeat_at=?,last_error_at=?,last_error_code=?,"
            "details_json=? WHERE service=? AND instance_id=?",
            (timestamp, timestamp, type(error).__name__[:120], safe_details, service, instance),
        )
    elif success:
        conn.execute(
            "UPDATE service_status SET heartbeat_at=?,last_success_at=?,last_error_at=NULL,"
            "last_error_code=NULL,details_json=? WHERE service=? AND instance_id=?",
            (timestamp, timestamp, safe_details, service, instance),
        )
    else:
        conn.execute(
            "UPDATE service_status SET heartbeat_at=?,details_json=? WHERE service=? AND instance_id=?",
            (timestamp, safe_details, service, instance),
        )


def stop_service(conn: sqlite3.Connection, service: str, instance: str) -> None:
    timestamp = utc_now()
    with transaction(conn, immediate=True):
        changed = conn.execute(
            "UPDATE service_status SET heartbeat_at=?,stopped_at=? WHERE service=? AND instance_id=?",
            (timestamp, timestamp, service, instance),
        ).rowcount
        if changed:
            record(conn, f"service.{service}", "service.stopped", "service", service, "stopped")


def service_health(row: sqlite3.Row | None, *, now: datetime | None = None,
                   gmail_poll_seconds: int = 60) -> str:
    if row is None:
        return "missing"
    if row["stopped_at"]:
        return "stopped"
    now = now or datetime.now(timezone.utc)
    heartbeat_at = datetime.fromisoformat(str(row["heartbeat_at"]))
    threshold = max(180, 3 * gmail_poll_seconds) if row["service"] == "gmail" else (
        90 if row["service"] == "maintenance" else 30
    )
    if now - heartbeat_at > timedelta(seconds=threshold):
        return "stale"
    if row["last_error_at"] and (not row["last_success_at"]
                                 or str(row["last_error_at"]) > str(row["last_success_at"])):
        return "error"
    return "healthy"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    kind: str
    size_bytes: int
    sha256: str
    encrypted_path: Path | None = None


def verify_backup(path: Path) -> BackupInfo:
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("Backup file not found")
    uri = path.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        version = current_version(conn)
    finally:
        conn.close()
    if integrity != "ok":
        raise ValueError("Backup integrity check failed")
    if version != latest_version():
        raise ValueError(f"Backup schema {version} does not match expected schema {latest_version()}")
    return BackupInfo(path, "manual", path.stat().st_size, _sha256(path))


def _encrypt_age(source: Path, export_dir: Path, recipient: str) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    target = export_dir / f"{source.name}.age"
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        subprocess.run(
            ["age", "--recipient", recipient, "--output", str(temporary), str(source)],
            check=True, capture_output=True, text=True,
        )
        temporary.replace(target)
        return target
    except (OSError, subprocess.CalledProcessError) as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Encrypted backup export failed") from exc


def create_backup(database_path: Path, backup_dir: Path, kind: str = "manual", *,
                  export_dir: Path | None = None, age_recipient: str = "") -> BackupInfo:
    if kind not in BACKUP_KINDS:
        raise ValueError("Invalid backup kind")
    database_path, backup_dir = Path(database_path), Path(backup_dir)
    if not database_path.is_file():
        raise ValueError("Database file not found")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"rrpp-bridge-{kind}-{stamp}.db"
    temporary = target.with_suffix(".tmp")
    source_conn = sqlite3.connect(database_path)
    target_conn = sqlite3.connect(temporary)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    temporary.replace(target)
    verification_error: Exception | None = None
    try:
        checked = verify_backup(target)
        integrity_status = "verified"
    except Exception as exc:
        verification_error = exc
        integrity_status = "failed"
        checked = BackupInfo(target, kind, target.stat().st_size, _sha256(target))
    encrypted_path = None
    export_error: Exception | None = None
    if integrity_status == "verified" and age_recipient and export_dir:
        try:
            encrypted_path = _encrypt_age(target, export_dir, age_recipient)
        except Exception as exc:
            export_error = exc
    info = BackupInfo(target, kind, checked.size_bytes, checked.sha256, encrypted_path)
    conn = connect(database_path)
    try:
        timestamp = utc_now()
        with transaction(conn, immediate=True):
            backup_id = f"bak_{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO backup_records VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                (backup_id, timestamp, kind, target.name, info.size_bytes, info.sha256,
                 integrity_status,
                 int(encrypted_path is not None), timestamp if integrity_status == "verified" else None),
            )
            record(conn, "maintenance", "backup.created", "backup", backup_id, integrity_status,
                   {"kind": kind, "filename": target.name, "encrypted_export": bool(encrypted_path)})
    finally:
        conn.close()
    if verification_error:
        raise ValueError("Created backup failed verification") from verification_error
    if export_error:
        raise RuntimeError("Backup was verified but encrypted export failed") from export_error
    return info


def apply_retention(database_path: Path, backup_dir: Path, export_dir: Path | None = None) -> list[str]:
    conn = connect(database_path)
    removed: list[str] = []
    try:
        for kind, keep in (("daily", 7), ("monthly", 3)):
            rows = conn.execute(
                "SELECT * FROM backup_records WHERE kind=? AND integrity_status='verified' "
                "ORDER BY created_at DESC", (kind,),
            ).fetchall()
            for row in rows[keep:]:
                path = Path(backup_dir) / row["filename"]
                path.unlink(missing_ok=True)
                if export_dir:
                    (Path(export_dir) / f"{row['filename']}.age").unlink(missing_ok=True)
                conn.execute("DELETE FROM backup_records WHERE id=?", (row["id"],))
                record(conn, "maintenance", "backup.pruned", "backup", row["id"], "deleted",
                       {"kind": kind, "filename": row["filename"]})
                removed.append(str(row["filename"]))
    finally:
        conn.close()
    return removed


def _decrypt_age(source: Path, identity: Path, target: Path) -> None:
    try:
        subprocess.run(
            ["age", "--decrypt", "--identity", str(identity), "--output", str(target), str(source)],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Encrypted backup decryption failed") from exc


def restore_backup(database_path: Path, backup_path: Path, backup_dir: Path, *,
                   confirmation: str, identity: Path | None = None) -> Path:
    if confirmation != "RESTORE":
        raise PermissionError("Restore requires literal confirmation RESTORE")
    database_path, backup_path = Path(database_path), Path(backup_path)
    conn = connect(database_path)
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
        active = [row["service"] for row in conn.execute(
            "SELECT service,heartbeat_at FROM service_status WHERE stopped_at IS NULL"
        ).fetchall() if datetime.fromisoformat(str(row["heartbeat_at"])) >= cutoff]
    finally:
        conn.close()
    if active:
        raise RuntimeError(f"Stop active services before restore: {', '.join(active)}")
    decrypted: Path | None = None
    source = backup_path
    if backup_path.suffix == ".age":
        if not identity:
            raise ValueError("Encrypted restore requires an age identity")
        handle, temporary_name = tempfile.mkstemp(suffix=".db", dir=Path(backup_dir))
        os.close(handle)
        decrypted = Path(temporary_name)
        decrypted.unlink()
        _decrypt_age(backup_path, Path(identity), decrypted)
        source = decrypted
    try:
        verify_backup(source)
        safety = create_backup(database_path, backup_dir, "pre_restore")
        source_conn = sqlite3.connect(source)
        target_conn = sqlite3.connect(database_path)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
            source_conn.close()
        try:
            verify_backup(database_path)
        except Exception:
            rollback_source = sqlite3.connect(safety.path)
            rollback_target = sqlite3.connect(database_path)
            try:
                rollback_source.backup(rollback_target)
            finally:
                rollback_target.close()
                rollback_source.close()
            raise
        conn = connect(database_path)
        try:
            with transaction(conn, immediate=True):
                conn.execute("UPDATE service_status SET stopped_at=?,heartbeat_at=?",
                             (utc_now(), utc_now()))
                conn.execute("UPDATE backup_records SET restored_at=? WHERE filename=?",
                             (utc_now(), source.name))
                record(conn, "cli.restore", "backup.restored", "database", "primary", "completed",
                       {"source_filename": backup_path.name, "safety_filename": safety.path.name})
        finally:
            conn.close()
        return safety.path
    finally:
        if decrypted:
            decrypted.unlink(missing_ok=True)


def _backup_due(conn: sqlite3.Connection, kind: str, local_now: datetime) -> bool:
    row = conn.execute(
        "SELECT created_at FROM backup_records WHERE kind=? ORDER BY created_at DESC LIMIT 1",
        (kind,),
    ).fetchone()
    if not row:
        return True
    last = datetime.fromisoformat(str(row["created_at"])).astimezone(local_now.tzinfo)
    return (last.year, last.month) != (local_now.year, local_now.month) if kind == "monthly" else last.date() != local_now.date()


def run_maintenance(settings, *, once: bool = False) -> None:
    conn = connect(settings.database_path)
    instance = instance_id("maintenance")
    start_service(conn, "maintenance", instance)
    try:
        while True:
            local_now = datetime.now(ZoneInfo(settings.backup_timezone))
            heartbeat(conn, "maintenance", instance)
            if once or local_now.hour >= settings.backup_hour:
                try:
                    kind = "monthly" if _backup_due(conn, "monthly", local_now) else "daily"
                    if once or _backup_due(conn, kind, local_now):
                        info = create_backup(
                            settings.database_path, settings.backup_dir, kind,
                            export_dir=settings.backup_export_dir,
                            age_recipient=settings.backup_age_recipient,
                        )
                        apply_retention(settings.database_path, settings.backup_dir,
                                        settings.backup_export_dir)
                        heartbeat(conn, "maintenance", instance, success=True,
                                  details={"backup": info.path.name, "kind": kind})
                except Exception as exc:
                    heartbeat(conn, "maintenance", instance, error=exc)
                    if once:
                        raise
            if once:
                return
            time.sleep(30)
    finally:
        stop_service(conn, "maintenance", instance)
        conn.close()
