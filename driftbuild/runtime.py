"""Runtime re-entry for installed, zipapp, and frozen Drift distributions."""

from __future__ import annotations

import importlib
import runpy
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import cast

_INTERNAL_MARKER = "__drift_internal__"
_SCRIPT_MARKER = "__drift_script__"
_PIP_MARKER = "__drift_pip__"
_CONAN_MARKER = "__drift_conan__"
_INTERNAL_MODULES = frozenset(
    {
        "driftbuild.action",
        "driftbuild.adapter_action",
        "driftbuild.autotools",
        "driftbuild.bundle",
        "driftbuild.conan",
        "driftbuild.opaque",
        "driftbuild.prebuilt",
        "driftbuild.vcpkg",
    }
)


def module_command(module: str) -> tuple[str, ...]:
    """Return a command that re-enters a trusted Drift helper module."""
    if module not in _INTERNAL_MODULES:
        raise ValueError(f"Unsupported internal Drift module: {module}")
    executable = sys.executable
    if getattr(sys, "frozen", False):
        return executable, _INTERNAL_MARKER, module
    launcher = Path(sys.argv[0]).resolve()
    if launcher.suffix == ".pyz":
        return executable, str(launcher), _INTERNAL_MARKER, module
    return executable, "-m", module


def script_command(script: Path) -> tuple[str, ...]:
    """Return a command for one managed Python tool entry point."""
    executable = sys.executable
    if getattr(sys, "frozen", False):
        return executable, _SCRIPT_MARKER, str(script.resolve())
    return executable, str(script.resolve())


def _external_path(path: Path) -> None:
    value = str(path.resolve())
    if value not in sys.path:
        sys.path.insert(0, value)


def internal_dispatch(arguments: Sequence[str]) -> int | None:
    """Run a trusted helper request, or return None for a normal CLI invocation."""
    if arguments and arguments[0] == _SCRIPT_MARKER:
        if len(arguments) < 2:
            raise ValueError("Managed Drift script path is missing")
        script = Path(arguments[1]).resolve()
        trusted_layout = script.name == "drift-meson.py" and len(script.parents) >= 3
        trusted_layout = trusted_layout and script.parents[1].name == "meson" and script.parents[2].name == "tools"
        if not trusted_layout:
            raise ValueError(f"Managed Drift script has an invalid tool-store layout: {script}")
        previous = sys.argv
        sys.argv = [str(script), *arguments[2:]]
        _external_path(script.parent)
        try:
            runpy.run_path(str(script), run_name="__main__")
        finally:
            sys.argv = previous
        return 0
    if arguments and arguments[0] == _PIP_MARKER:
        pip_main = cast(Callable[[list[str]], int | None], importlib.import_module("pip._internal.cli.main").main)
        return int(pip_main(list(arguments[1:])) or 0)
    if arguments and arguments[0] == _CONAN_MARKER:
        from driftbuild.bootstrap import CONAN_VERSION
        from driftbuild.storage import tool_store_root

        _external_path(tool_store_root() / "conan" / CONAN_VERSION / "site-packages")
        conan_main = cast(Callable[[list[str]], int | None], importlib.import_module("conan.cli.cli").main)
        return int(conan_main(list(arguments[1:])) or 0)
    if len(arguments) < 2 or arguments[0] != _INTERNAL_MARKER:
        return None
    module_name = arguments[1]
    if module_name not in _INTERNAL_MODULES:
        raise ValueError(f"Unsupported internal Drift module: {module_name}")
    module: ModuleType = importlib.import_module(module_name)
    entry = getattr(module, "main", None)
    if not callable(entry):
        raise ValueError(f"Internal Drift module has no main function: {module_name}")
    previous = sys.argv
    sys.argv = [module_name, *arguments[2:]]
    try:
        result = entry()
    finally:
        sys.argv = previous
    return int(result or 0)
