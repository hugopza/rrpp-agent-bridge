from __future__ import annotations

import argparse
import json
import socket
import time
from wsgiref.simple_server import make_server

from .config import Settings
from .db import backup_database, connect, current_version, initialize
from .queue import JobQueue
from .runtime import get_mode, initialize_mode
from .service import process_one
from .web import Application


def _prepare(settings: Settings):
    conn = connect(settings.database_path)
    initialize(conn)
    initialize_mode(conn, settings.mode)
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(prog="rrpp-bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init-db", "migrate", "status", "recover-stale", "web"):
        sub.add_parser(command)
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env(require_auth=args.command == "web")

    if args.command == "migrate":
        backup = backup_database(settings.database_path)
        conn = connect(settings.database_path)
        applied = initialize(conn)
        initialize_mode(conn, settings.mode)
        print(json.dumps({"applied": applied, "backup": str(backup) if backup else None}))
        return

    conn = _prepare(settings)
    if args.command == "init-db":
        print(f"Initialized {settings.database_path} at schema version {current_version(conn)}")
        return
    if args.command == "status":
        counts = {row["state"]: row["count"] for row in conn.execute(
            "SELECT state,count(*) count FROM jobs GROUP BY state"
        )}
        print(json.dumps({"database": str(settings.database_path), "schema": current_version(conn),
                          "mode": get_mode(conn), "jobs": counts}, sort_keys=True))
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
    worker_id = f"worker.{socket.gethostname()}"
    while True:
        processed = process_one(conn, worker_id, settings.max_attempts,
                                settings.lease_seconds, settings.canary_senders)
        if args.once:
            break
        if not processed:
            time.sleep(1)


if __name__ == "__main__":
    main()
