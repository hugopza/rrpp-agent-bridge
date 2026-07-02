from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VALID_MODES = frozenset({"shadow", "dry-run", "canary", "live"})
ENV_KEY = re.compile(r"^(?:RRPP_[A-Z0-9_]+|INSTAGRAM_(?:VERIFY_TOKEN|APP_SECRET|PAGE_ACCESS_TOKEN|BUSINESS_ACCOUNT_ID))$")


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
        if not ENV_KEY.fullmatch(key):
            raise ValueError(f"Invalid .env key on line {number}")
        os.environ.setdefault(key, value.strip())


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
    gmail_client_path: Path = Path("secrets/gmail-oauth-client.json")
    gmail_token_path: Path = Path("secrets/gmail-token.json")
    gmail_poll_seconds: int = 60
    gmail_batch_size: int = 50
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
    instagram_port: int = 8081

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
            gmail_poll_seconds = int(os.getenv("RRPP_GMAIL_POLL_SECONDS", "60"))
            gmail_batch_size = int(os.getenv("RRPP_GMAIL_BATCH_SIZE", "50"))
            backup_hour = int(os.getenv("RRPP_BACKUP_HOUR", "3"))
            instagram_port = int(os.getenv("RRPP_INSTAGRAM_PORT", "8081"))
        except ValueError as exc:
            raise ValueError("Port, max attempts, and lease seconds must be integers") from exc
        if (not 1 <= port <= 65535 or max_attempts < 1 or lease_seconds < 5
                or gmail_poll_seconds < 15 or not 1 <= gmail_batch_size <= 500
                or not 0 <= backup_hour <= 23 or not 1 <= instagram_port <= 65535):
            raise ValueError("Invalid port, retry, lease, or Gmail polling configuration")
        instagram_enabled_value = os.getenv("RRPP_INSTAGRAM_ENABLED", "false").strip().casefold()
        if instagram_enabled_value not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
            raise ValueError("RRPP_INSTAGRAM_ENABLED must be a boolean value")
        instagram_enabled = instagram_enabled_value in {"1", "true", "yes", "on"}
        instagram_verify_token = os.getenv("INSTAGRAM_VERIFY_TOKEN", "").strip()
        instagram_app_secret = os.getenv("INSTAGRAM_APP_SECRET", "").strip()
        instagram_business_account_id = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "").strip()
        if instagram_enabled and not all((instagram_verify_token, instagram_app_secret,
                                          instagram_business_account_id)):
            raise ValueError("Enabled Instagram webhook requires verify token, app secret, and business account ID")
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
            gmail_client_path=Path(os.getenv("RRPP_GMAIL_CLIENT_PATH", "secrets/gmail-oauth-client.json")),
            gmail_token_path=Path(os.getenv("RRPP_GMAIL_TOKEN_PATH", "secrets/gmail-token.json")),
            gmail_poll_seconds=gmail_poll_seconds,
            gmail_batch_size=gmail_batch_size,
            backup_dir=Path(os.getenv("RRPP_BACKUP_DIR", "backups")),
            backup_export_dir=Path(os.getenv("RRPP_BACKUP_EXPORT_DIR", "backup-export")),
            backup_age_recipient=os.getenv("RRPP_BACKUP_AGE_RECIPIENT", "").strip(),
            backup_hour=backup_hour,
            backup_timezone=backup_timezone,
            instagram_enabled=instagram_enabled,
            instagram_verify_token=instagram_verify_token,
            instagram_app_secret=instagram_app_secret,
            instagram_page_access_token=os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN", "").strip(),
            instagram_business_account_id=instagram_business_account_id,
            instagram_port=instagram_port,
        )
