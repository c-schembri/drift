"""Shared package binary cache identities."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from driftbuild.configuration import config_payload
from driftbuild.model import BuildConfig, PackageSpec
from driftbuild.storage import drift_home
from driftbuild.versions import CMAKE_VERSION, CONAN_VERSION, MESON_VERSION, VCPKG_VERSION


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
    adapter_versions = {
        "cmake": CMAKE_VERSION,
        "conan": CONAN_VERSION,
        "meson": MESON_VERSION,
        "vcpkg": VCPKG_VERSION,
    }
    toolchain_digest: str | None = None
    if config.toolchain_file is not None and config.toolchain_file.is_file():
        toolchain_digest = hashlib.sha256(config.toolchain_file.read_bytes()).hexdigest()
    environment = {} if config.hermetic else {
        name: os.environ[name]
        for name in (
            "CC",
            "CXX",
            "AR",
            "SDKROOT",
            "MACOSX_DEPLOYMENT_TARGET",
            "WindowsSdkDir",
            "VCToolsVersion",
        )
        if name in os.environ
    }
    payload = {
        "schema": 2,
        "source": source_root.name if len(source_root.name) == 64 else str(source_root.resolve()),
        "package": package.name if isinstance(package, PackageSpec) else package,
        "adapter": adapter,
        "adapter_version": adapter_versions.get(adapter, "host"),
        "options": package.options if isinstance(package, PackageSpec) else (),
        "features": package.features if isinstance(package, PackageSpec) else (),
        "components": package.components if isinstance(package, PackageSpec) else (),
        "linkage": package.linkage if isinstance(package, PackageSpec) else "auto",
        "config": config_payload(config),
        "toolchain_digest": toolchain_digest,
        "environment": environment,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return binary_cache_root() / hashlib.sha256(encoded).hexdigest()
