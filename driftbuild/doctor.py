"""Actionable environment and project diagnostics."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from driftbuild import __version__
from driftbuild.configuration import config_key
from driftbuild.errors import DriftError
from driftbuild.model import BuildConfig, GitSource, ProjectSpec
from driftbuild.packages import packages_fetch
from driftbuild.storage import drift_home
from driftbuild.toolchain import toolchain_resolve

DiagnosticStatus = Literal["ok", "warning", "error"]


@dataclass(frozen=True)
class Diagnostic:
    """One named diagnostic result with a machine-readable status."""

    name: str
    status: DiagnosticStatus
    detail: str


def _compiler_check(config: BuildConfig, state_root: Path) -> Diagnostic:
    try:
        toolchain = toolchain_resolve(config, state_root)
    except DriftError as error:
        return Diagnostic("toolchain", "error", str(error))
    missing = [
        executable
        for executable in dict.fromkeys((toolchain.cc, toolchain.cxx, toolchain.linker, toolchain.archiver))
        if shutil.which(executable, path=toolchain.environment.get("PATH")) is None
    ]
    if missing:
        return Diagnostic("toolchain", "error", f"missing executables: {', '.join(missing)}")
    return Diagnostic(
        "toolchain",
        "ok",
        f"{toolchain.family}: {toolchain.cc}, {toolchain.cxx}, {toolchain.linker}, {toolchain.archiver}",
    )


def _packages_check(project: ProjectSpec, root: Path) -> Diagnostic:
    if not project.packages:
        return Diagnostic("packages", "ok", "no locked packages declared")
    try:
        roots = packages_fetch(project, root, offline=True)
    except DriftError as error:
        return Diagnostic("packages", "error", str(error))
    return Diagnostic("packages", "ok", f"{len(roots)} locked source package(s) verified offline")


def doctor_run(project: ProjectSpec, root: Path, config: BuildConfig) -> dict[str, Any]:
    """Check the active project, toolchain, lock state, and required host tools."""
    checks: list[Diagnostic] = []
    python_status: DiagnosticStatus = "ok" if sys.version_info[:2] == (3, 12) else "error"
    checks.append(Diagnostic("python", python_status, sys.version.split()[0]))
    writable = os.access(root, os.W_OK)
    checks.append(
        Diagnostic("project", "ok" if writable else "error", f"{root} ({'writable' if writable else 'read-only'})")
    )
    checks.append(_compiler_check(config, root / ".drift"))
    if any(isinstance(package.source, GitSource) for package in project.packages):
        git = shutil.which("git")
        checks.append(Diagnostic("git", "ok" if git else "error", git or "git was not found on PATH"))
    checks.append(_packages_check(project, root))
    home = drift_home()
    checks.append(Diagnostic("cache", "ok", str(home)))
    return {
        "drift_version": __version__,
        "configuration": config_key(config),
        "ok": all(check.status != "error" for check in checks),
        "checks": [asdict(check) for check in checks],
    }
