"""Low-overhead no-op build path before loading the full project platform."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from driftbuild import __version__

_OPERATIONS = {
    "artifact",
    "benchmark",
    "build",
    "clean",
    "command",
    "configure",
    "fetch",
    "generate",
    "graph",
    "lock",
    "release",
    "remote",
    "run",
    "task",
    "test",
}
_VALUE_OPTIONS = {"--root", "--compiler", "--architecture", "--build-type", "-D", "--define"}


def _value(arguments: list[str], name: str, default: str) -> str:
    if name in arguments:
        index = arguments.index(name)
        if index + 1 < len(arguments):
            return arguments[index + 1]
    prefix = name + "="
    return next((item[len(prefix) :] for item in arguments if item.startswith(prefix)), default)


def _root_find(arguments: list[str]) -> Path | None:
    selected = _value(arguments, "--root", "")
    if selected:
        return Path(selected).resolve()
    current = Path.cwd().resolve()
    return next((path for path in (current, *current.parents) if (path / "drift.toml").is_file()), None)


def _architecture() -> str:
    machine = platform.machine()
    return {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64"}.get(machine.casefold(), machine.casefold())


def _inputs_current(path: Path) -> bool:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state["drift_version"] != __version__:
            return False
        inputs: dict[str, int] = state["inputs"]
        return all(Path(source).stat().st_mtime_ns == modified for source, modified in inputs.items())
    except (AttributeError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _operation_find(arguments: list[str]) -> tuple[str, int] | None:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in _OPERATIONS:
            return argument, index
        if argument in _VALUE_OPTIONS:
            index += 2
            continue
        if argument.startswith(("--root=", "--compiler=", "--architecture=", "--build-type=", "--define=", "-D")):
            index += 1
            continue
        if argument in ("-v", "--offline"):
            index += 1
            continue
        return None
    return None


def _no_op(arguments: list[str]) -> bool:
    operation_found = _operation_find(arguments)
    if operation_found is None or operation_found[0] != "build":
        return False
    if any(item == "-D" or item.startswith(("-D", "--define")) for item in arguments):
        return False
    operation = operation_found[1]
    target_arguments = arguments[operation + 1 :]
    directory = Path(target_arguments[0]).resolve() if target_arguments else None
    root: Path | None
    if directory is not None and directory.is_dir() and (directory / "drift.toml").is_file():
        root = directory
        target_arguments = target_arguments[1:]
    else:
        root = _root_find(arguments[:operation])
    if root is None:
        return False
    compiler = _value(arguments[:operation], "--compiler", "auto")
    architecture = _value(arguments[:operation], "--architecture", _architecture())
    build_type = _value(arguments[:operation], "--build-type", "debug")
    key = f"{sys.platform}-{architecture}-{compiler}-{build_type}"
    build_root = root / ".drift" / "build" / key
    ninja = root / ".drift" / "tools" / "ninja" / "1.13.1" / ("ninja.exe" if os.name == "nt" else "ninja")
    if not ninja.is_file() or not (build_root / "build.ninja").is_file():
        return False
    if not _inputs_current(build_root / "configured.json"):
        return False
    completed = subprocess.run(
        [str(ninja), "-n", "-f", "build.ninja", *target_arguments],
        cwd=build_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0 and "no work to do" in completed.stdout


def main(argv: list[str] | None = None) -> int:
    """Exit quickly for a proven no-op build, otherwise load the complete CLI."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    started = time.perf_counter()
    if _no_op(arguments):
        print(f"Build timing: total {time.perf_counter() - started:.3f}s | no work")
        return 0
    from driftbuild.cli import main as cli_main

    return cli_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
