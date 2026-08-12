"""Shared Drift state locations."""

from __future__ import annotations

import os
from pathlib import Path


def drift_home() -> Path:
    """Return the shared Drift cache root for the current user."""
    override = os.environ.get("DRIFT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "drift"
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / "drift"


def tool_store_root() -> Path:
    """Return the shared installation root for managed build tools."""
    return drift_home() / "tools"
