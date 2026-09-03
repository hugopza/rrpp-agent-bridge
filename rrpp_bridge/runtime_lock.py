from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path
from types import TracebackType
from typing import Any


class DatabaseRuntimeLock:
    """Advisory database lifecycle lock used by the supported Linux deployment."""

    def __init__(self, database_path: Path, *, exclusive: bool, blocking: bool = True):
        self._handle = None
        if os.name != "posix":
            return
        fcntl: Any = import_module("fcntl")

        lock_path = Path(database_path).with_suffix(Path(database_path).suffix + ".runtime.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = lock_path.open("a+b")
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(self._handle.fileno(), operation)
        except BlockingIOError as exc:
            self.close()
            raise RuntimeError("Stop all bridge processes before restoring SQLite") from exc

    def close(self) -> None:
        if self._handle is None:
            return
        fcntl: Any = import_module("fcntl")

        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> "DatabaseRuntimeLock":
        return self

    def __exit__(self, _type: type[BaseException] | None, _value: BaseException | None,
                 _traceback: TracebackType | None) -> None:
        self.close()
