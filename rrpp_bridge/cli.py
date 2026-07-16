from __future__ import annotations

import argparse
import json
import signal
import socket
import time
from pathlib import Path
from urllib.request import urlopen
from wsgiref.simple_server import make_server

from .agent_provider import AgentContext, AgentProviderError, build_agent_provider
from .config import Settings, VALID_MODES
from .db import backup_database, connect, current_version, initialize, latest_version, prepare_runtime
from .queue import JobQueue
from .instagram_sender import build_instagram_sender
from .operations import (create_backup, instance_id, restore_backup, run_maintenance,
                         SERVICES, service_health, start_service, stop_service, verify_backup,
                         heartbeat)
from .runtime import get_mode, initialize_mode, set_mode
from .service import process_one
from .web import Application


class GracefulShutdown(Exception):
    pass


def _install_shutdown_handlers() -> None:
    def shutdown(_signum, _frame):
        raise GracefulShutdown()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)


def _prepare(settings: Settings):
    conn = connect(settings.database_path)
    try:
        prepare_runtime(conn)
        initialize_mode(conn, settings.mode)
    except Exception:
        conn.close()
        raise
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(prog="rrpp-bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init-db", "migrate", "status", "recover-stale", "web",
                    "instagram-webhook", "agent-check"):
        sub.add_parser(command)
    mode_command = sub.add_parser("set-mode")
    mode_command.add_argument("mode", choices=sorted(VALID_MODES))
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    maintenance = sub.add_parser("maintenance")
    maintenance.add_argument("--once", action="store_true")
    backup = sub.add_parser("backup")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_sub.add_parser("create")
    backup_create.add_argument("--kind", choices=("manual", "daily", "monthly"), default="manual")
    backup_verify = backup_sub.add_parser("verify")
    backup_verify.add_argument("path")
    restore = sub.add_parser("restore")
    restore.add_argument("path")
    restore.add_argument("--confirm", required=True)
    restore.add_argument("--identity")
    healthcheck = sub.add_parser("healthcheck")
    healthcheck.add_argument("service", choices=("web", "worker", "maintenance"))
    args = parser.parse_args()
    settings = Settings.from_env(require_auth=args.command == "web")

    if args.command == "agent-check":
        provider = build_agent_provider(settings)
        try:
            decision = provider.generate_decision(AgentContext(
                correlation_id="agent-check", conversation_id="agent-check-v2",
                channel="local", receiver_account_id="local-check",
                external_user_id="local-check", language_hint="ca",
                incoming_message="Hola", history=(), catalog_items=(), bot_paused=False,
            ))
        except AgentProviderError as exc:
            print(json.dumps({"provider": provider.provider_id, "error": exc.code,
                              "diagnostic": exc.diagnostic}, sort_keys=True))
            raise SystemExit(1) from None
        print(json.dumps({
            "provider": provider.provider_id, "action": decision.action,
            "language": decision.language, "reason_code": decision.reason_code,
            "structured": decision.structured, "text_length": len(decision.text),
        }, sort_keys=True))
        return

    if args.command == "backup":
        if args.backup_command == "verify":
            info = verify_backup(Path(args.path))
        else:
            info = create_backup(
                settings.database_path, settings.backup_dir, args.kind,
                export_dir=settings.backup_export_dir,
                age_recipient=settings.backup_age_recipient,
            )
        print(json.dumps({"path": str(info.path), "size_bytes": info.size_bytes,
                          "sha256": info.sha256, "encrypted_path": str(info.encrypted_path) if info.encrypted_path else None}))
        return

    if args.command == "restore":
        safety = restore_backup(settings.database_path, Path(args.path), settings.backup_dir,
                                confirmation=args.confirm,
                                identity=Path(args.identity) if args.identity else None)
        print(json.dumps({"restored": str(args.path), "safety_backup": str(safety)}))
        return

    if args.command == "migrate":
        existed = settings.database_path.exists()
        conn = connect(settings.database_path)
        backup = (backup_database(settings.database_path)
                  if existed and current_version(conn) < latest_version() else None)
        applied = initialize(conn)
        initialize_mode(conn, settings.mode)
        print(json.dumps({"applied": applied, "backup": str(backup) if backup else None}))
        conn.close()
        return

    conn = _prepare(settings)
    if args.command == "healthcheck":
        if args.service == "web":
            try:
                with urlopen(f"http://127.0.0.1:{settings.port}/login", timeout=3) as response:
                    healthy = response.status == 200
            except OSError:
                healthy = False
            state = "healthy" if healthy else "unreachable"
        else:
            row = conn.execute("SELECT * FROM service_status WHERE service=?", (args.service,)).fetchone()
            state = service_health(row)
            healthy = state == "healthy"
        print(json.dumps({"service": args.service, "status": state}))
        conn.close()
        raise SystemExit(0 if healthy else 1)
    if args.command == "init-db":
        print(f"Initialized {settings.database_path} at schema version {current_version(conn)}")
        return
    if args.command == "status":
        counts = {row["state"]: row["count"] for row in conn.execute(
            "SELECT state,count(*) count FROM jobs GROUP BY state"
        )}
        active_rows = [row for row in conn.execute("SELECT * FROM service_status")
                       if row["service"] in SERVICES]
        services = {row["service"]: service_health(row) for row in active_rows}
        instances = {row["service"]: row["instance_id"] for row in active_rows}
        print(json.dumps({"database": str(settings.database_path), "schema": current_version(conn),
                          "mode": get_mode(conn), "jobs": counts, "services": services,
                          "instances": instances}, sort_keys=True))
        return
    if args.command == "set-mode":
        set_mode(conn, args.mode, "cli")
        print(json.dumps({"mode": get_mode(conn)}))
        return
    if args.command == "recover-stale":
        recovered = JobQueue(conn).recover_stale(settings.max_attempts, "cli.recover-stale")
        print(json.dumps({"recovered": recovered}))
        return
    if args.command == "web":
        conn.close()
        app = Application(settings)
        print(f"Dashboard listening on http://{settings.host}:{settings.port}")
        with make_server(settings.host, settings.port, app) as server:
            server.serve_forever()
        return
    if args.command == "instagram-webhook":
        from .instagram_webhook import InstagramWebhookApplication
        conn.close()
        app = InstagramWebhookApplication(settings)
        print(f"Instagram webhook listening on http://{settings.host}:{settings.instagram_port}")
        with make_server(settings.host, settings.instagram_port, app) as server:
            server.serve_forever()
        return
    if args.command == "maintenance":
        conn.close()
        _install_shutdown_handlers()
        try:
            run_maintenance(settings, once=args.once)
        except GracefulShutdown:
            pass
        return
    worker_id = f"worker.{socket.gethostname()}"
    instance = instance_id("worker")
    start_service(conn, "worker", instance)
    _install_shutdown_handlers()
    last_heartbeat = 0.0
    agent_provider = build_agent_provider(settings)
    instagram_sender = build_instagram_sender(settings)
    try:
        while True:
            processed = process_one(conn, worker_id, settings.max_attempts,
                                    settings.lease_seconds, settings.canary_senders,
                                    agent_provider, instagram_sender)
            now = time.monotonic()
            if processed or now - last_heartbeat >= 10:
                heartbeat(conn, "worker", instance, success=processed,
                          details={"processed": bool(processed)})
                last_heartbeat = now
            if args.once:
                break
            if not processed:
                time.sleep(1)
    except GracefulShutdown:
        pass
    finally:
        stop_service(conn, "worker", instance)
        conn.close()


if __name__ == "__main__":
    main()
