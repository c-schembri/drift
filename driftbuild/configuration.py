"""Stable configuration identities shared by build and package backends."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from driftbuild.model import BuildConfig


def config_payload(config: BuildConfig) -> dict[str, object]:
    """Return the complete deterministic identity of a selected configuration."""
    toolchain_sha256 = None
    if config.toolchain_file is not None and config.toolchain_file.is_file():
        toolchain_sha256 = hashlib.sha256(config.toolchain_file.read_bytes()).hexdigest()
    return {
        "platform": config.platform,
        "architecture": config.architecture,
        "compiler": config.compiler,
        "build_type": config.build_type,
        "values": sorted(config.values.items()),
        "target": config.target,
        "sysroot": str(config.sysroot.resolve()) if config.sysroot is not None else None,
        "toolchain_file": str(config.toolchain_file.resolve()) if config.toolchain_file is not None else None,
        "toolchain_sha256": toolchain_sha256,
        "sanitizers": list(config.sanitizers),
        "coverage": config.coverage,
        "lto": config.lto,
        "warnings": config.warnings,
        "unity_size": config.unity_size,
        "profile": config.profile,
        "hermetic": config.hermetic,
    }


def config_key(config: BuildConfig) -> str:
    """Return a readable configuration key with a collision-resistant suffix."""
    target = config.target or config.architecture
    readable = "-".join((config.platform, target, config.compiler, config.build_type))
    readable = re.sub(r"[^A-Za-z0-9_.-]", "_", readable)
    if (
        not config.values
        and config.target is None
        and config.sysroot is None
        and config.toolchain_file is None
        and not config.sanitizers
        and not config.coverage
        and not config.lto
        and config.warnings == "default"
        and not config.unity_size
        and config.profile == "host"
        and not config.hermetic
    ):
        return readable
    encoded = json.dumps(config_payload(config), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{readable}-{hashlib.sha256(encoded).hexdigest()[:10]}"


def path_resolve(root: Path, value: Path | None) -> Path | None:
    """Resolve an optional configuration path relative to a project root."""
    if value is None:
        return None
    return value.resolve() if value.is_absolute() else (root / value).resolve()
