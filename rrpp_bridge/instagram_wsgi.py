from __future__ import annotations

from .config import Settings
from .instagram_webhook import InstagramWebhookApplication

application = InstagramWebhookApplication(Settings.from_env(require_auth=False))
