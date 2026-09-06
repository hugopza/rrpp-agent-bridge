from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProductionDeploymentTests(unittest.TestCase):
    def test_compose_keeps_internal_ports_and_container_hardening(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        for expected in (
            '"127.0.0.1:${RRPP_PUBLIC_PORT:-8080}:8080"',
            '"127.0.0.1:${RRPP_INSTAGRAM_PUBLIC_PORT:-8081}:8081"',
            "read_only: true",
            "cap_drop:\n    - ALL",
            "no-new-privileges:true",
            'profiles: ["container-worker"]',
            '["CMD", "rrpp-bridge", "healthcheck", "instagram"]',
        ):
            self.assertIn(expected, compose)
        self.assertNotIn('"8080:8080"', compose)
        self.assertNotIn('"8081:8081"', compose)
        self.assertIn("USER 10001:10001", dockerfile)

    def test_gunicorn_uses_only_tmpfs_and_disables_the_unused_control_socket(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn("/tmp:size=32m,mode=1777", compose)
        for command in (dockerfile, compose):
            self.assertIn('"--no-control-socket"', command)
            self.assertIn('"--worker-tmp-dir", "/tmp"', command)
            self.assertNotIn("/home/rrpp/.gunicorn", command)
        for expected in (
            "check_gunicorn_logs",
            "Control server error",
            "Failed to start control socket",
            "Read-only file system.*[/]home[/]rrpp[/]\\.gunicorn",
            'logs --since "${DEPLOY_STARTED_AT}" web instagram',
        ):
            self.assertIn(expected, deploy)

    def test_nginx_exposes_only_the_exact_webhook_backend(self):
        nginx = (ROOT / "deploy" / "nginx" / "rrpp-agent-bridge.conf.example").read_text(
            encoding="utf-8"
        )
        self.assertEqual(2, nginx.count("location = /webhooks/instagram"))
        self.assertEqual(2, nginx.count("location /"))
        self.assertIn("proxy_pass http://127.0.0.1:8081;", nginx)
        self.assertNotIn("127.0.0.1:8080", nginx)
        self.assertNotIn("18789", nginx)

    def test_systemd_worker_is_unprivileged_and_hardened(self):
        unit = (ROOT / "deploy" / "systemd" / "rrpp-agent-bridge-worker.service").read_text(
            encoding="utf-8"
        )
        for expected in (
            "User=rrpp",
            "Group=rrpp",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "PrivateTmp=true",
            "ExecStart=/opt/rrpp-agent-bridge/.venv/bin/rrpp-bridge worker",
            "ExecStartPost=/opt/rrpp-agent-bridge/.venv/bin/rrpp-bridge healthcheck worker",
        ):
            self.assertIn(expected, unit)

    def test_deploy_prepares_and_verifies_shared_storage_before_migration(self):
        storage = (ROOT / "scripts" / "prepare-production-storage.sh").read_text(
            encoding="utf-8"
        )
        deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

        for directory in ("var", "backups", "backup-export"):
            self.assertIn(f'"${{APP_DIR}}/{directory}"', storage)
        for expected in (
            "CONTAINER_UID=${RRPP_CONTAINER_UID:-10001}",
            "CONTAINER_GID=${RRPP_CONTAINER_GID:-10001}",
            'runuser -u "${HOST_USER}"',
            'setpriv --reuid="${CONTAINER_UID}" --regid="${CONTAINER_GID}"',
            'find -P "${directory}" -type f -exec setfacl',
            'find -P "${directory}" -type d -exec setfacl',
            'default_acl="d:u:${HOST_USER}:rwx"',
            "umask 0077",
        ):
            self.assertIn(expected, storage)

        bootstrap = 'bash "${APP_DIR}/scripts/prepare-production-storage.sh"'
        migration = '"${COMPOSE[@]}" --profile tools run --rm migrate'
        self.assertIn(bootstrap, deploy)
        self.assertLess(deploy.index(bootstrap), deploy.index(migration))

    def test_production_template_contains_no_example_secret_values(self):
        environment = (ROOT / ".env.production.example").read_text(encoding="utf-8")
        for key in (
            "RRPP_DASHBOARD_USER",
            "RRPP_DASHBOARD_PASSWORD",
            "RRPP_SESSION_SECRET",
            "INSTAGRAM_VERIFY_TOKEN",
            "INSTAGRAM_APP_SECRET",
            "INSTAGRAM_PAGE_ACCESS_TOKEN",
            "OPENCLAW_GATEWAY_TOKEN",
        ):
            self.assertIn(f"{key}=\n", environment)
        self.assertNotIn("RRPP_VENUE_KNOWLEDGE_DIR", environment)

    def test_local_template_also_fails_closed_without_default_credentials(self):
        environment = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("RRPP_DASHBOARD_PASSWORD=\n", environment)
        self.assertIn("RRPP_SESSION_SECRET=\n", environment)


if __name__ == "__main__":
    unittest.main()
