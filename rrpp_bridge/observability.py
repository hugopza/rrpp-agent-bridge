from __future__ import annotations

import json
import sys
from typing import Any


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, separators=(",", ":"), sort_keys=True),
          file=sys.stdout, flush=True)
