from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VALID_MODES = frozenset({"shadow", "dry-run", "canary", "live"})
RRPP_ENV_KEYS = frozenset({
    "RRPP_DATABASE_PATH", "RRPP_MODE", "RRPP_DASHBOARD_USER",
    "RRPP_DASHBOARD_PASSWORD", "RRPP_SESSION_SECRET", "RRPP_HOST", "RRPP_PORT",
    "RRPP_MAX_ATTEMPTS", "RRPP_LEASE_SECONDS", "RRPP_RESPONSE_DEBOUNCE_SECONDS",
    "RRPP_CANARY_SENDERS", "RRPP_BACKUP_DIR", "RRPP_BACKUP_EXPORT_DIR",
    "RRPP_BACKUP_AGE_RECIPIENT", "RRPP_BACKUP_HOUR", "RRPP_BACKUP_TIMEZONE",
    "RRPP_INSTAGRAM_ENABLED", "RRPP_INSTAGRAM_PORT", "RRPP_INSTAGRAM_SEND_ENABLED",
    "RRPP_INSTAGRAM_GRAPH_BASE_URL", "RRPP_INSTAGRAM_GRAPH_API_VERSION",
    "RRPP_INSTAGRAM_SEND_TIMEOUT_SECONDS",
})
ENV_KEY = re.compile(
    r"^(?:"
    r"INSTAGRAM_(?:VERIFY_TOKEN|APP_SECRET|PAGE_ACCESS_TOKEN|BUSINESS_ACCOUNT_ID|"
    r"WEBHOOK_ACCOUNT_ID|ACCOUNTS_JSON|ACCOUNT_[A-Z][A-Z0-9_]{0,31}_ACCESS_TOKEN)|"
    r"OPENCLAW_(?:ENABLED|BASE_URL|AGENT_ID|AGENT_NAME|TIMEOUT_SECONDS|GATEWAY_TOKEN))$"
)
INSTAGRAM_ACCOUNT_ALIAS = re.compile(r"[a-z][a-z0-9_]{0,31}")


def load_local_env(path: Path = Path(".env")) -> None:
    """Load the project's minimal KEY=VALUE format without overriding process env."""
    if not path.is_file():
        return
    for number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env entry on line {number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in RRPP_ENV_KEYS and not ENV_KEY.fullmatch(key):
            raise ValueError(f"Invalid .env key on line {number}")
        os.environ.setdefault(key, value.strip())


@dataclass(frozen=True)
class InstagramAccountSettings:
    alias: str
    webhook_account_id: str
    business_account_id: str
    access_token: str = ""


def _instagram_accounts_from_json(raw: str, *, send_enabled: bool) -> tuple[InstagramAccountSettings, ...]:
    if len(raw) > 32_768:
        raise ValueError("INSTAGRAM_ACCOUNTS_JSON is too large")
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("INSTAGRAM_ACCOUNTS_JSON must be valid JSON") from exc
    if not isinstance(values, list) or not values:
        raise ValueError("INSTAGRAM_ACCOUNTS_JSON must be a non-empty array")
    accounts: list[InstagramAccountSettings] = []
    aliases: set[str] = set()
    webhook_ids: set[str] = set()
    business_ids: set[str] = set()
    allowed_keys = {"alias", "webhook_account_id", "business_account_id"}
    for value in values:
        if not isinstance(value, dict) or set(value) != allowed_keys:
            raise ValueError(
                "Each Instagram account must contain only alias, webhook_account_id, "
                "and business_account_id"
            )
        alias = value.get("alias")
        webhook_id = value.get("webhook_account_id")
        business_id = value.get("business_account_id")
        if not isinstance(alias, str) or not INSTAGRAM_ACCOUNT_ALIAS.fullmatch(alias):
            raise ValueError("Instagram account aliases must use lowercase letters, digits, or underscores")
        if (not isinstance(webhook_id, str) or not webhook_id.strip()
                or len(webhook_id.strip()) > 200):
            raise ValueError("Instagram webhook account IDs must contain 1 to 200 characters")
        if (not isinstance(business_id, str) or not business_id.strip()
                or len(business_id.strip()) > 200):
            raise ValueError("Instagram business account IDs must contain 1 to 200 characters")
        alias, webhook_id, business_id = alias.strip(), webhook_id.strip(), business_id.strip()
        if alias in aliases or webhook_id.casefold() in webhook_ids:
            raise ValueError("Instagram account aliases and webhook account IDs must be unique")
        if business_id.casefold() in business_ids:
            raise ValueError("Instagram business account IDs must be unique")
        token = os.getenv(f"INSTAGRAM_ACCOUNT_{alias.upper()}_ACCESS_TOKEN", "").strip()
        if send_enabled and not token:
            raise ValueError(f"Instagram account {alias} requires its access token environment variable")
        aliases.add(alias)
        webhook_ids.add(webhook_id.casefold())
        business_ids.add(business_id.casefold())
        accounts.append(InstagramAccountSettings(alias, webhook_id, business_id, token))
    return tuple(accounts)


@dataclass(frozen=True)
class Settings:
    database_path: Path
    mode: str
    dashboard_user: str
    dashboard_password: str
    session_secret: str
    host: str = "127.0.0.1"
    port: int = 8080
    max_attempts: int = 3
    lease_seconds: int = 60
    canary_senders: frozenset[str] = frozenset()
    backup_dir: Path = Path("backups")
    backup_export_dir: Path = Path("backup-export")
    backup_age_recipient: str = ""
    backup_hour: int = 3
    backup_timezone: str = "Europe/Madrid"
    instagram_enabled: bool = False
    instagram_verify_token: str = ""
    instagram_app_secret: str = ""
    instagram_page_access_token: str = ""
    instagram_business_account_id: str = ""
    instagram_webhook_account_id: str = ""
    instagram_accounts: tuple[InstagramAccountSettings, ...] = ()
    instagram_port: int = 8081
    instagram_send_enabled: bool = False
    instagram_graph_base_url: str = "https://graph.instagram.com"
    instagram_graph_api_version: str = "v24.0"
    instagram_send_timeout_seconds: float = 15.0
    response_debounce_seconds: int = 0
    openclaw_enabled: bool = False
    openclaw_base_url: str = "http://127.0.0.1:18789"
    openclaw_agent_id: str = "rrpp"
    openclaw_timeout_seconds: float = 60.0
    openclaw_gateway_token: str = ""

    def configured_instagram_accounts(self) -> tuple[InstagramAccountSettings, ...]:
        if self.instagram_accounts:
            return self.instagram_accounts
        if self.instagram_webhook_account_id or self.instagram_business_account_id:
            return (InstagramAccountSettings(
                "legacy", self.instagram_webhook_account_id,
                self.instagram_business_account_id, self.instagram_page_access_token,
            ),)
        return ()

    def instagram_webhook_account_ids(self) -> frozenset[str]:
        return frozenset(
            account.webhook_account_id for account in self.configured_instagram_accounts()
        )

    @classmethod
    def from_env(cls, *, require_auth: bool = True) -> "Settings":
        load_local_env()
        mode = os.getenv("RRPP_MODE", "shadow")
        if mode not in VALID_MODES:
            raise ValueError(f"RRPP_MODE must be one of: {', '.join(sorted(VALID_MODES))}")
        user = os.getenv("RRPP_DASHBOARD_USER", "")
        password = os.getenv("RRPP_DASHBOARD_PASSWORD", "")
        secret = os.getenv("RRPP_SESSION_SECRET", "")
        if require_auth and (not user or len(password) < 12 or len(secret) < 32):
            raise ValueError(
                "Dashboard credentials are required; password must be at least 12 "
                "characters and session secret at least 32 characters"
            )
        try:
            port = int(os.getenv("RRPP_PORT", "8080"))
            max_attempts = int(os.getenv("RRPP_MAX_ATTEMPTS", "3"))
            lease_seconds = int(os.getenv("RRPP_LEASE_SECONDS", "60"))
            backup_hour = int(os.getenv("RRPP_BACKUP_HOUR", "3"))
            instagram_port = int(os.getenv("RRPP_INSTAGRAM_PORT", "8081"))
            response_debounce_seconds = int(os.getenv("RRPP_RESPONSE_DEBOUNCE_SECONDS", "3"))
            instagram_send_timeout_seconds = float(os.getenv("RRPP_INSTAGRAM_SEND_TIMEOUT_SECONDS", "15"))
            openclaw_timeout_seconds = float(os.getenv("OPENCLAW_TIMEOUT_SECONDS", "60"))
        except ValueError as exc:
            raise ValueError("Ports, retry values, leases, and timeouts must be numeric") from exc
        if (not 1 <= port <= 65535 or max_attempts < 1 or lease_seconds < 5
                or not 0 <= backup_hour <= 23 or not 1 <= instagram_port <= 65535
                or not 0 <= response_debounce_seconds <= 30
                or not 1 <= instagram_send_timeout_seconds <= 60
                or not 1 <= openclaw_timeout_seconds <= 120):
            raise ValueError("Invalid port, retry, lease, or timeout configuration")
        instagram_enabled_value = os.getenv("RRPP_INSTAGRAM_ENABLED", "false").strip().casefold()
        if instagram_enabled_value not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
            raise ValueError("RRPP_INSTAGRAM_ENABLED must be a boolean value")
        instagram_enabled = instagram_enabled_value in {"1", "true", "yes", "on"}
        instagram_verify_token = os.getenv("INSTAGRAM_VERIFY_TOKEN", "").strip()
        instagram_app_secret = os.getenv("INSTAGRAM_APP_SECRET", "").strip()
        instagram_business_account_id = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "").strip()
        instagram_webhook_account_id = os.getenv(
            "INSTAGRAM_WEBHOOK_ACCOUNT_ID", instagram_business_account_id
        ).strip()
        instagram_page_access_token = os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN", "").strip()
        instagram_send_value = os.getenv("RRPP_INSTAGRAM_SEND_ENABLED", "false").strip().casefold()
        if instagram_send_value not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
            raise ValueError("RRPP_INSTAGRAM_SEND_ENABLED must be a boolean value")
        instagram_send_enabled = instagram_send_value in {"1", "true", "yes", "on"}
        accounts_json = os.getenv("INSTAGRAM_ACCOUNTS_JSON", "").strip()
        if accounts_json:
            if any((instagram_page_access_token, instagram_business_account_id,
                    os.getenv("INSTAGRAM_WEBHOOK_ACCOUNT_ID", "").strip())):
                raise ValueError(
                    "INSTAGRAM_ACCOUNTS_JSON cannot be combined with legacy Instagram account variables"
                )
            instagram_accounts = _instagram_accounts_from_json(
                accounts_json, send_enabled=instagram_send_enabled
            )
            instagram_business_account_id = ""
            instagram_webhook_account_id = ""
            # Explicitly remove the legacy credential when registry mode is selected.
            instagram_page_access_token = ""  # nosec B105
        else:
            instagram_accounts = ()
        configured_accounts = instagram_accounts or (
            (InstagramAccountSettings(
                "legacy", instagram_webhook_account_id,
                instagram_business_account_id, instagram_page_access_token,
            ),) if instagram_webhook_account_id or instagram_business_account_id else ()
        )
        if instagram_enabled and (not instagram_verify_token or not instagram_app_secret
                                  or not configured_accounts):
            raise ValueError(
                "Enabled Instagram webhook requires verify token, app secret, and configured accounts"
            )
        if instagram_send_enabled and (not instagram_enabled or any(
                not account.business_account_id or not account.access_token
                for account in configured_accounts)):
            raise ValueError(
                "Instagram sending requires the enabled webhook and complete account credentials"
            )
        instagram_graph_base_url = os.getenv(
            "RRPP_INSTAGRAM_GRAPH_BASE_URL", "https://graph.instagram.com"
        ).strip().rstrip("/")
        parsed_graph_url = urlparse(instagram_graph_base_url)
        if (parsed_graph_url.scheme != "https" or parsed_graph_url.hostname != "graph.instagram.com"
                or parsed_graph_url.username or parsed_graph_url.password or parsed_graph_url.port
                or parsed_graph_url.path not in {"", "/"} or parsed_graph_url.query
                or parsed_graph_url.fragment):
            raise ValueError("RRPP_INSTAGRAM_GRAPH_BASE_URL must be https://graph.instagram.com")
        instagram_graph_api_version = os.getenv(
            "RRPP_INSTAGRAM_GRAPH_API_VERSION", "v24.0"
        ).strip()
        if not re.fullmatch(r"v[1-9][0-9]?\.0", instagram_graph_api_version):
            raise ValueError("RRPP_INSTAGRAM_GRAPH_API_VERSION must look like v24.0")
        openclaw_enabled_value = os.getenv("OPENCLAW_ENABLED", "false").strip().casefold()
        if openclaw_enabled_value not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
            raise ValueError("OPENCLAW_ENABLED must be a boolean value")
        openclaw_enabled = openclaw_enabled_value in {"1", "true", "yes", "on"}
        openclaw_base_url = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789").strip().rstrip("/")
        parsed_openclaw_url = urlparse(openclaw_base_url)
        try:
            _ = parsed_openclaw_url.port
        except ValueError as exc:
            raise ValueError("OPENCLAW_BASE_URL contains an invalid port") from exc
        if (parsed_openclaw_url.scheme != "http"
                or parsed_openclaw_url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed_openclaw_url.username or parsed_openclaw_url.password
                or parsed_openclaw_url.query or parsed_openclaw_url.fragment
                or parsed_openclaw_url.path not in {"", "/"}):
            raise ValueError("OPENCLAW_BASE_URL must be a loopback HTTP origin without credentials or a path")
        openclaw_agent_id = (
            os.getenv("OPENCLAW_AGENT_ID") or os.getenv("OPENCLAW_AGENT_NAME") or "rrpp"
        ).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", openclaw_agent_id):
            raise ValueError("OPENCLAW_AGENT_ID must be a simple agent identifier")
        openclaw_gateway_token = os.getenv("OPENCLAW_GATEWAY_TOKEN", "").strip()
        if openclaw_enabled and not openclaw_gateway_token:
            raise ValueError("Enabled OpenClaw requires OPENCLAW_GATEWAY_TOKEN")
        canary_senders = frozenset(
            value.strip().casefold() for value in os.getenv("RRPP_CANARY_SENDERS", "").split(",")
            if value.strip()
        )
        backup_timezone = os.getenv("RRPP_BACKUP_TIMEZONE", "Europe/Madrid")
        try:
            ZoneInfo(backup_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("RRPP_BACKUP_TIMEZONE must be a valid IANA timezone") from exc
        return cls(
            database_path=Path(os.getenv("RRPP_DATABASE_PATH", "var/rrpp-bridge.db")),
            mode=mode,
            dashboard_user=user,
            dashboard_password=password,
            session_secret=secret,
            host=os.getenv("RRPP_HOST", "127.0.0.1"),
            port=port,
            max_attempts=max_attempts,
            lease_seconds=lease_seconds,
            canary_senders=canary_senders,
            backup_dir=Path(os.getenv("RRPP_BACKUP_DIR", "backups")),
            backup_export_dir=Path(os.getenv("RRPP_BACKUP_EXPORT_DIR", "backup-export")),
            backup_age_recipient=os.getenv("RRPP_BACKUP_AGE_RECIPIENT", "").strip(),
            backup_hour=backup_hour,
            backup_timezone=backup_timezone,
            instagram_enabled=instagram_enabled,
            instagram_verify_token=instagram_verify_token,
            instagram_app_secret=instagram_app_secret,
            instagram_page_access_token=instagram_page_access_token,
            instagram_business_account_id=instagram_business_account_id,
            instagram_webhook_account_id=instagram_webhook_account_id,
            instagram_accounts=instagram_accounts,
            instagram_port=instagram_port,
            instagram_send_enabled=instagram_send_enabled,
            instagram_graph_base_url=instagram_graph_base_url,
            instagram_graph_api_version=instagram_graph_api_version,
            instagram_send_timeout_seconds=instagram_send_timeout_seconds,
            response_debounce_seconds=response_debounce_seconds,
            openclaw_enabled=openclaw_enabled,
            openclaw_base_url=openclaw_base_url,
            openclaw_agent_id=openclaw_agent_id,
            openclaw_timeout_seconds=openclaw_timeout_seconds,
            openclaw_gateway_token=openclaw_gateway_token,
        )
