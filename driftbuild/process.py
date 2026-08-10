"""Process execution, cancellation, and secret-aware command rendering."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from driftbuild.errors import ExecutionError


def command_render(arguments: Sequence[str], secrets: Sequence[str] = ()) -> str:
    """Render a command while replacing exact and embedded secret values."""
    rendered = subprocess.list2cmdline([str(argument) for argument in arguments])
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        rendered = rendered.replace(secret, "***")
    return rendered


@dataclass(frozen=True)
class ProcessResult:
    """Captured process result."""

    arguments: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class OwnedProcess:
    """Process whose complete child tree is stopped on context exit."""

    def __init__(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        stdout: int | IO[Any] | None = None,
        stderr: int | IO[Any] | None = None,
    ):
        if os.name == "nt":
            creationflags = 0x00000200 | 0x08000000
            start_new_session = False
        else:
            creationflags = 0
            start_new_session = True
        self._process: subprocess.Popen[bytes] = subprocess.Popen(
            list(arguments),
            cwd=cwd,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
        self._lock = threading.Lock()

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            if self._process.poll() is not None:
                return
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.kill(-self.pid, signal.SIGTERM)
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    self._process.kill()
                else:
                    os.kill(-self.pid, getattr(signal, "SIGKILL", 9))
                self._process.wait()

    def __enter__(self) -> OwnedProcess:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.stop()


def run(
    arguments: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
    capture: bool = False,
    check: bool = True,
) -> ProcessResult:
    """Run one bounded process and return a stable result."""
    try:
        completed = subprocess.run(
            list(arguments),
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            timeout=timeout_seconds,
            capture_output=capture,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ExecutionError(f"Command timed out: {command_render(arguments)}") from error
    except OSError as error:
        raise ExecutionError(f"Cannot start command {command_render(arguments)}: {error}") from error
    result = ProcessResult(
        tuple(str(value) for value in arguments),
        completed.returncode,
        completed.stdout or "",
        completed.stderr or "",
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise ExecutionError(f"Command failed ({result.returncode}): {command_render(arguments)}{suffix}")
    return result
