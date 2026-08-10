"""Low-overhead no-op build path before loading the full project platform."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from driftbuild import __version__


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
    machine = (
        os.environ.get("PROCESSOR_ARCHITECTURE", "") if os.name == "nt" else str(os.uname().machine)  # type: ignore[attr-defined]
    )
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


def _no_op(arguments: list[str]) -> bool:
    if "build" not in arguments or any(item == "-D" or item.startswith(("-D", "--define")) for item in arguments):
        return False
    operation = arguments.index("build")
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
        [str(ninja), "-n", "-f", "build.ninja", *arguments[operation + 1 :]],
        cwd=build_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0 and "no work to do" in completed.stdout


def main(argv: list[str] | None = None) -> int:
    """Exit quickly for a proven no-op build, otherwise load the complete CLI."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if _no_op(arguments):
        return 0
    from driftbuild.cli import main as cli_main

    return cli_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
