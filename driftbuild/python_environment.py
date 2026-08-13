"""Content-addressed Python dependencies for project provider commands."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from driftbuild.errors import ExecutionError
from driftbuild.locking import cache_lock
from driftbuild.model import ProjectSpec
from driftbuild.runtime import pip_command
from driftbuild.storage import drift_home


def _identity(requirements: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    digest.update(sys.implementation.cache_tag.encode())
    for path in requirements:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


def _path_prepend(name: str, path: Path) -> None:
    value = str(path.resolve())
    current = os.environ.get(name)
    values = current.split(os.pathsep) if current else []
    if value not in values:
        os.environ[name] = os.pathsep.join((value, *values))


def python_environment_activate(project: ProjectSpec, _state_root: Path, *, offline: bool = False) -> Path | None:
    """Materialise and activate the project's locked provider requirements."""
    if not project.python_requirements:
        return None
    home = drift_home()
    destination = home / "python" / _identity(project.python_requirements)
    marker = destination / ".complete"
    with cache_lock(home / "locks" / "python-environment.lock"):
        if not marker.is_file():
            if offline:
                raise ExecutionError("Project Python requirements are not materialized in offline mode")
            staging = destination.with_name(f".{destination.name}-{os.getpid()}")
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            command = [
                *pip_command(),
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--target",
                str(staging),
            ]
            for requirements in project.python_requirements:
                command.extend(("--requirement", str(requirements)))
            result = subprocess.run(command, check=False)
            if result.returncode != 0:
                shutil.rmtree(staging, ignore_errors=True)
                raise ExecutionError(f"Cannot materialize project Python requirements (exit {result.returncode})")
            (staging / ".complete").write_text("ok\n", encoding="utf-8")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(staging, destination)
    value = str(destination.resolve())
    if value not in sys.path:
        sys.path.insert(0, value)
    _path_prepend("PYTHONPATH", destination)
    _path_prepend("DRIFT_PROJECT_SITE", destination)
    return destination
