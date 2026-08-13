"""Shared package binary cache identities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from driftbuild.configuration import config_payload
from driftbuild.model import BuildConfig, PackageSpec
from driftbuild.storage import drift_home


def binary_cache_root() -> Path:
    """Return the shared package binary cache root."""
    return drift_home() / "binaries"


def package_build_root(
    source_root: Path,
    package: PackageSpec | str,
    config: BuildConfig,
    adapter: str,
) -> Path:
    """Return a shared build root keyed by source, options, toolchain, and adapter."""
    payload = {
        "source": source_root.name if len(source_root.name) == 64 else str(source_root.resolve()),
        "package": package.name if isinstance(package, PackageSpec) else package,
        "adapter": adapter,
        "options": package.options if isinstance(package, PackageSpec) else (),
        "features": package.features if isinstance(package, PackageSpec) else (),
        "config": config_payload(config),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return binary_cache_root() / hashlib.sha256(encoded).hexdigest()
