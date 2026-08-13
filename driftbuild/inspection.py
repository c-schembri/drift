"""Resolved package graph and provenance inspection."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from driftbuild.errors import ConfigurationError
from driftbuild.importers import adapter_detect, package_provenance, project_import
from driftbuild.model import BuildConfig, PackageSpec, ProjectSpec
from driftbuild.package_cache import package_build_root
from driftbuild.packages import package_lock_read, packages_fetch


def _output(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = {"path": str(path)}
    if path.is_file():
        value["size"] = path.stat().st_size
        value["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        value["materialized"] = False
    return value


def _package_inspect(
    package: PackageSpec,
    source_root: Path,
    config: BuildConfig,
    state_root: Path,
    offline: bool,
) -> dict[str, Any]:
    adapter = adapter_detect(source_root, package, config.platform)
    payload: dict[str, Any] = {
        "name": package.name,
        "adapter": adapter,
        "options": dict(package.options),
        "features": list(package.features),
        "components": list(package.components),
        "linkage": package.linkage,
        "source_root": str(source_root),
        "binary_cache": str(package_build_root(source_root, package, config, adapter)),
        "provenance": package_provenance(source_root, package, config.platform),
    }
    if package.overlay is not None or (source_root / "drift.toml").is_file():
        payload["interface"] = "drift"
        return payload
    try:
        imported = project_import(source_root, state_root, config, package, offline=offline)
    except ConfigurationError as error:
        payload["configuration_error"] = str(error)
        return payload
    payload["targets"] = [
        {
            "name": target.name,
            "kind": target.kind,
            "include_dirs": [str(path) for path in target.include_dirs],
            "outputs": [_output(path) for path in target.outputs],
            "command": list(target.action.command) if target.action is not None else None,
        }
        for target in imported.targets
    ]
    return payload


def packages_inspect(
    project: ProjectSpec,
    root: Path,
    config: BuildConfig,
    names: tuple[str, ...] = (),
    offline: bool = False,
) -> dict[str, Any]:
    """Return resolved adapter, cache, command, output, and lock provenance data."""
    selected = [package for package in project.packages if not names or package.name in names]
    missing = sorted(set(names) - {package.name for package in selected})
    if missing:
        raise ConfigurationError(f"Unknown packages: {', '.join(missing)}")
    locked = {package.name: package for package in package_lock_read(root).packages}
    roots = packages_fetch(project, root, offline=offline, verify_cached=False)
    packages: list[dict[str, Any]] = []
    for package in selected:
        payload = _package_inspect(package, roots[package.name], config, root / ".drift" / "imports", offline)
        lock = locked[package.name]
        payload["lock"] = {
            "request_sha256": lock.request_sha256,
            "content_sha256": lock.content_sha256,
            "provenance": dict(lock.provenance),
        }
        packages.append(payload)
    return {"configuration": config.build_type, "packages": packages}
