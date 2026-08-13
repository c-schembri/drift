"""Low-overhead no-op build path before loading the full project platform."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import TypedDict, cast

from driftbuild import __version__
from driftbuild.configuration import config_key
from driftbuild.model import BuildConfig
from driftbuild.storage import tool_store_root
from driftbuild.versions import NINJA_VERSION

_OPERATIONS = {
    "artifact",
    "audit",
    "benchmark",
    "bootstrap",
    "build",
    "cache",
    "clean",
    "command",
    "configure",
    "completion",
    "doctor",
    "fetch",
    "generate",
    "graph",
    "inspect",
    "lock",
    "matrix",
    "output",
    "perf",
    "release",
    "remote",
    "run",
    "self-update",
    "task",
    "targets",
    "test",
}
_VALUE_OPTIONS = {
    "--root",
    "--compiler",
    "--architecture",
    "--build-type",
    "--target",
    "--sysroot",
    "--toolchain",
    "-D",
    "--define",
}


class _ConfiguredState(TypedDict):
    environment: dict[str, str]
    environment_removed: list[str]
    output_phases: dict[str, str]
    configuration_environment: dict[str, str | None]


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


def _definitions(arguments: list[str]) -> dict[str, str] | None:
    values: dict[str, str] = {}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        item: str | None = None
        if argument in ("-D", "--define"):
            index += 1
            if index >= len(arguments):
                return None
            item = arguments[index]
        elif argument.startswith("--define="):
            item = argument.removeprefix("--define=")
        elif argument.startswith("-D"):
            item = argument.removeprefix("-D")
        if item is not None:
            if "=" not in item:
                return None
            name, value = item.split("=", 1)
            values[name] = value
        index += 1
    return values


def _ninja_path() -> Path:
    override = os.environ.get("DRIFT_NINJA")
    if override:
        return Path(override).expanduser().resolve()
    return tool_store_root() / "ninja" / NINJA_VERSION / ("ninja.exe" if os.name == "nt" else "ninja")


def _state_load(path: Path) -> _ConfiguredState | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return None
        state = cast(dict[str, object], loaded)
        if state["drift_version"] != __version__:
            return None
        inputs = state["inputs"]
        directories = state["directories"]
        environment = state["environment"]
        environment_removed = state["environment_removed"]
        output_phases = state["output_phases"]
        configuration_environment = state["configuration_environment"]
        if not isinstance(inputs, dict) or not isinstance(directories, dict):
            return None
        if not all(isinstance(source, str) and isinstance(modified, int) for source, modified in inputs.items()):
            return None
        if not all(
            isinstance(directory, str) and isinstance(modified, int) for directory, modified in directories.items()
        ):
            return None
        if not all(Path(source).stat().st_mtime_ns == modified for source, modified in inputs.items()):
            return None
        if not all(Path(directory).stat().st_mtime_ns == modified for directory, modified in directories.items()):
            return None
        if not isinstance(environment, dict) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in environment.items()
        ):
            return None
        if not isinstance(environment_removed, list) or not all(isinstance(name, str) for name in environment_removed):
            return None
        if not isinstance(output_phases, dict) or not all(
            isinstance(output, str) and isinstance(phase, str) for output, phase in output_phases.items()
        ):
            return None
        if not isinstance(configuration_environment, dict) or not all(
            isinstance(name, str) and (value is None or isinstance(value, str))
            for name, value in configuration_environment.items()
        ):
            return None
        if any(os.environ.get(name) != value for name, value in configuration_environment.items()):
            return None
        return {
            "environment": cast(dict[str, str], environment),
            "environment_removed": cast(list[str], environment_removed),
            "output_phases": cast(dict[str, str], output_phases),
            "configuration_environment": cast(dict[str, str | None], configuration_environment),
        }
    except (AttributeError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _log_snapshot(path: Path) -> tuple[int, bytes]:
    if not path.is_file():
        return 0, b""
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - 128))
        return size, stream.read()


def _path_key(value: str) -> str:
    return value.replace("\\", "/").casefold()


def _phase_timings(path: Path, snapshot: tuple[int, bytes], phases: dict[str, str]) -> list[str]:
    size, tail = snapshot
    if not path.is_file() or path.stat().st_size < size:
        return []
    with path.open("rb") as stream:
        stream.seek(size - len(tail))
        if stream.read(len(tail)) != tail:
            return []
        stream.seek(size)
        lines = stream.read().decode("utf-8").splitlines()
    phase_keys = {_path_key(output): phase for output, phase in phases.items()}
    edges: dict[tuple[int, int, str], str] = {}
    for line in lines:
        fields = line.split("\t")
        if len(fields) != 5 or fields[0].startswith("#"):
            continue
        try:
            edge = int(fields[0]), int(fields[1]), fields[4]
        except ValueError:
            continue
        phase = phase_keys.get(_path_key(fields[3]))
        if phase is not None:
            edges[edge] = phase
    rendered = []
    for name in ("compile", "archive", "link", "action"):
        selected = [edge for edge, phase in edges.items() if phase == name]
        if selected:
            duration = sum(end - start for start, end, _hash in selected) / 1000
            count = len(selected)
            rendered.append(f"{name} {duration:.3f}s ({count} {'job' if count == 1 else 'jobs'})")
    return rendered


def _ninja_build_arguments(arguments: list[str], verbose: bool) -> tuple[list[str], bool] | None:
    result = ["-f", "build.ninja"]
    if verbose:
        result.append("-v")
    dry_run = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in ("-j", "--jobs"):
            index += 1
            if index >= len(arguments):
                return None
            try:
                jobs = int(arguments[index])
            except ValueError:
                return None
            if jobs < 1:
                return None
            result.extend(("-j", str(jobs)))
        elif argument.startswith("--jobs="):
            try:
                jobs = int(argument.split("=", 1)[1])
            except ValueError:
                return None
            if jobs < 1:
                return None
            result.extend(("-j", str(jobs)))
        elif argument == "--explain":
            result.extend(("-d", "explain"))
        elif argument == "--keep-going":
            result.extend(("-k", "0"))
        elif argument == "--dry-run":
            result.append("-n")
            dry_run = True
        elif argument.startswith("-"):
            return None
        else:
            result.append(argument)
        index += 1
    return result, dry_run


def _operation_find(arguments: list[str]) -> tuple[str, int] | None:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in _OPERATIONS:
            return argument, index
        if argument in _VALUE_OPTIONS:
            index += 2
            continue
        if argument.startswith(
            (
                "--root=",
                "--compiler=",
                "--architecture=",
                "--build-type=",
                "--target=",
                "--sysroot=",
                "--toolchain=",
                "--define=",
                "-D",
            )
        ):
            index += 1
            continue
        if argument in ("-v", "--verbose", "--offline", "--hermetic"):
            index += 1
            continue
        return None
    return None


def _cached_build(arguments: list[str], started: float) -> int | None:
    operation_found = _operation_find(arguments)
    if operation_found is None or operation_found[0] != "build":
        return None
    operation = operation_found[1]
    values = _definitions(arguments[:operation])
    if values is None:
        return None
    target_arguments = arguments[operation + 1 :]
    directory = Path(target_arguments[0]).resolve() if target_arguments else None
    root: Path | None
    if directory is not None and directory.is_dir() and (directory / "drift.toml").is_file():
        root = directory
        target_arguments = target_arguments[1:]
    else:
        root = _root_find(arguments[:operation])
    if root is None:
        return None
    compiler = _value(arguments[:operation], "--compiler", "auto")
    architecture = _value(arguments[:operation], "--architecture", _architecture())
    build_type = _value(arguments[:operation], "--build-type", "debug")
    target = _value(arguments[:operation], "--target", "") or None
    if target is not None:
        architecture = {"aarch64": "arm64", "amd64": "x86_64"}.get(target.split("-", 1)[0], target.split("-", 1)[0])
    target_key = target.casefold() if target is not None else ""
    selected_platform = (
        "win32"
        if "windows" in target_key or "mingw" in target_key
        else "darwin"
        if "darwin" in target_key or "apple" in target_key
        else "linux"
        if "linux" in target_key
        else sys.platform
    )
    sysroot = _value(arguments[:operation], "--sysroot", "")
    toolchain = _value(arguments[:operation], "--toolchain", "")
    config = BuildConfig(
        selected_platform,
        architecture,
        compiler,
        build_type,
        values,
        target=target,
        sysroot=Path(sysroot).resolve() if sysroot else None,
        toolchain_file=Path(toolchain).resolve() if toolchain else None,
        hermetic="--hermetic" in arguments[:operation],
    )
    key = config_key(config)
    build_root = root / ".drift" / "build" / key
    ninja = _ninja_path()
    if not ninja.is_file() or not (build_root / "build.ninja").is_file():
        return None
    state = _state_load(build_root / "configured.json")
    if state is None:
        return None
    selected = _ninja_build_arguments(target_arguments, any(value in ("-v", "--verbose") for value in arguments[:operation]))
    if selected is None:
        return None
    ninja_arguments, dry_run = selected
    environment = dict(os.environ)
    for name in state["environment_removed"]:
        environment.pop(name, None)
    environment.update(state["environment"])
    log_path = build_root / ".ninja_log"
    snapshot = _log_snapshot(log_path)
    ninja_started = time.perf_counter()
    completed = subprocess.run(
        [str(ninja), *ninja_arguments],
        cwd=build_root,
        env=environment,
        check=False,
    )
    ninja_seconds = time.perf_counter() - ninja_started
    if completed.returncode != 0:
        return completed.returncode
    phases = _phase_timings(log_path, snapshot, state["output_phases"])
    timing_values = [f"total {time.perf_counter() - started:.3f}s", f"ninja {ninja_seconds:.3f}s", *phases]
    if not phases:
        timing_values.append("dry run" if dry_run else "no work")
    print("Build timing: " + " | ".join(timing_values))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Exit quickly for a proven no-op build, otherwise load the complete CLI."""
    started = time.perf_counter()
    arguments = list(sys.argv[1:] if argv is None else argv)

    if getattr(sys, "frozen", False):
        os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if getattr(sys, "frozen", False) and Path(sys.argv[0]).stem.casefold() == "conan":
        arguments = ["__drift_conan__", *arguments]
    if arguments and arguments[0].startswith("__drift_"):
        from driftbuild.runtime import internal_dispatch

        internal_result = internal_dispatch(arguments)
        if internal_result is not None:
            return internal_result
    cached_result = _cached_build(arguments, started)
    if cached_result is not None:
        return cached_result
    from driftbuild.cli import main as cli_main

    return cli_main(arguments, command_started=started)


if __name__ == "__main__":
    raise SystemExit(main())
