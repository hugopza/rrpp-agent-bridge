from __future__ import annotations

from .config import Settings
from .web import Application

application = Application(Settings.from_env())
