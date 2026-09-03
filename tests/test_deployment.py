from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductionDeploymentTests(unittest.TestCase):
    def test_compose_keeps_internal_ports_and_container_hardening(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
