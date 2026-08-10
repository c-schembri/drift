"""Explicit SSH remote execution service."""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

from driftbuild.model import RemoteSpec
from driftbuild.process import ProcessResult, run


def _ssh_base(remote: RemoteSpec) -> list[str]:
    destination = f"{remote.user}@{remote.host}" if remote.user else remote.host
    arguments = ["ssh", "-p", str(remote.port)]
    if remote.identity_file is not None:
        arguments.extend(("-i", str(remote.identity_file)))
    arguments.append(destination)
    return arguments


def remote_run(
    remote: RemoteSpec,
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> ProcessResult:
    """Run a command through the system SSH client without implicit synchronization."""
    assignments = [f"{name}={value}" for name, value in sorted((environment or {}).items())]
    remote_command = shlex.join([*assignments, *command])
    return run([*_ssh_base(remote), "--", remote_command], timeout_seconds=timeout_seconds, capture=True)


def remote_copy(remote: RemoteSpec, sources: Sequence[Path], destination: str) -> ProcessResult:
    """Copy explicit files through the system SCP client."""
    target = f"{remote.user}@{remote.host}:{destination}" if remote.user else f"{remote.host}:{destination}"
    arguments = ["scp", "-P", str(remote.port)]
    if remote.identity_file is not None:
        arguments.extend(("-i", str(remote.identity_file)))
    return run([*arguments, *map(str, sources), target], capture=True)
