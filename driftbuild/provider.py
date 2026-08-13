"""Stable process and filesystem services for project provider handlers."""

from __future__ import annotations

import errno
import importlib
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Literal


def run(arguments: list[str] | tuple[str, ...], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    """Run one command, raising on failure."""
    print(subprocess.list2cmdline([str(value) for value in arguments]), flush=True)
    subprocess.run(arguments, cwd=cwd, env=env, check=True)


def process_tree_start(arguments: list[str] | tuple[str, ...], **kwargs: Any) -> subprocess.Popen[Any]:
    """Start a process in an independently stoppable process group."""
    if os.name == "nt":
        creation_flags = kwargs.pop("creationflags", 0)
        kwargs["creationflags"] = creation_flags | 0x00000200 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(arguments, **kwargs)


def process_tree_stop(process: subprocess.Popen[Any], timeout: float = 10.0) -> None:
    """Stop a process and all descendants within a bounded interval."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if result.returncode != 0 and process.poll() is None:
            process.kill()
            process.wait(timeout=timeout)
            raise RuntimeError(f"Failed to stop process tree {process.pid}: {result.stdout.strip()}")
    else:
        try:
            os.kill(-process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.kill(-process.pid, getattr(signal, "SIGKILL", 9))
        process.wait(timeout=timeout)


class OwnedProcess:
    """Provider process whose complete child tree is stopped on context exit."""

    def __init__(self, arguments: list[str] | tuple[str, ...], **kwargs: Any):
        self._process = process_tree_start(arguments, **kwargs)
        self._stop_lock = threading.Lock()

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def stop(self, timeout: float = 10.0) -> None:
        with self._stop_lock:
            process_tree_stop(self._process, timeout=timeout)

    def __enter__(self) -> OwnedProcess:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> Literal[False]:
        self.stop()
        return False


class FileLock:
    """Cross-platform advisory lock with blocking and non-blocking acquisition."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.stream: BinaryIO | None = None

    def acquire(self, blocking: bool) -> bool:
        assert self.stream is None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        try:
            if os.name == "nt":
                msvcrt = importlib.import_module("msvcrt")
                while True:
                    try:
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, 13):
                            raise
                        if not blocking:
                            stream.close()
                            return False
                        time.sleep(0.1)
            else:
                fcntl = importlib.import_module("fcntl")
                flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                fcntl.flock(stream.fileno(), flags)
        except OSError as error:
            stream.close()
            if not blocking and error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, 13):
                return False
            raise
        self.stream = stream
        return True

    def release(self) -> None:
        if self.stream is None:
            return
        self.stream.seek(0)
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl = importlib.import_module("fcntl")
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()
        self.stream = None

    def __enter__(self) -> FileLock:
        self.acquire(True)
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> Literal[False]:
        self.release()
        return False


def require_path(path: Path, message: str) -> None:
    """Require one provider runtime input to exist."""
    if not path.exists():
        raise RuntimeError(message)


def env_prepend_paths(environment: dict[str, str], name: str, paths: list[Path] | tuple[Path, ...]) -> None:
    """Prepend paths using the host path separator."""
    prefix = os.pathsep.join(str(path) for path in paths if path)
    environment[name] = prefix + (os.pathsep + environment[name] if environment.get(name) else "")


def outputs_current(outputs: list[Path] | tuple[Path, ...], inputs: list[Path] | tuple[Path, ...]) -> bool:
    """Return whether every output is at least as new as every input."""
    if not outputs or any(not output.exists() for output in outputs):
        return False
    for path in inputs:
        require_path(path, f"Missing input: {path}")
    newest_input = max((path.stat().st_mtime_ns for path in inputs), default=0)
    return min(output.stat().st_mtime_ns for output in outputs) >= newest_input


def copy_file(source: Path, destination: Path) -> None:
    """Copy one changed file while preserving metadata."""
    require_path(source, f"Missing file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        source_stat = source.stat()
        destination_stat = destination.stat()
        if source_stat.st_size == destination_stat.st_size and source_stat.st_mtime_ns == destination_stat.st_mtime_ns:
            return
    shutil.copy2(source, destination)


def copy_tree_contents(source: Path, destination: Path) -> None:
    """Copy the files below one directory without replacing its root."""
    if not source.exists():
        return
    for root, directories, files in os.walk(source):
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        source_root = Path(root)
        destination_root = destination / source_root.relative_to(source)
        destination_root.mkdir(parents=True, exist_ok=True)
        for name in files:
            path = source_root / name
            if not path.is_symlink():
                copy_file(path, destination_root / name)


def remove_tree(path: Path, ignore_errors: bool = False) -> None:
    """Remove one directory tree, including read-only files."""
    def remove_readonly(function: Any, item_path: str, _error: Any) -> None:
        os.chmod(item_path, 0o700)
        function(item_path)

    shutil.rmtree(path, ignore_errors=ignore_errors, onerror=remove_readonly)


def remove_tree_retry(path: Path, attempts: int = 50, delay_seconds: float = 0.05) -> int:
    """Remove a tree, retrying transient Windows sharing violations."""
    assert attempts > 0
    for attempt in range(attempts):
        try:
            remove_tree(path)
            return attempt + 1
        except PermissionError as error:
            sharing_violation = getattr(error, "winerror", None) in (5, 32)
            if not sharing_violation or attempt + 1 == attempts:
                raise
            time.sleep(delay_seconds)
    raise AssertionError("unreachable")


def copy_files_to_dir(files: list[Path] | tuple[Path, ...], destination: Path) -> None:
    """Copy files into one flat destination directory."""
    for path in files:
        copy_file(path, destination / path.name)
