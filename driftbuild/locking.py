"""Cross-platform inter-process locks for shared Drift state."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from driftbuild.errors import ConfigurationError


def _try_lock(handle: BinaryIO) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        return True
    except BlockingIOError:
        return False


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@contextmanager
def cache_lock(path: Path, *, timeout_seconds: float = 600.0) -> Iterator[None]:
    """Serialize mutation of one shared cache entry across Drift processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise ConfigurationError(f"Timed out waiting for shared cache lock: {path}")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)
