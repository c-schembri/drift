"""Runtime re-entry for installed, zipapp, and frozen Drift distributions."""

from __future__ import annotations

import importlib
import os
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
_PROVIDER_MARKER = "__drift_provider__"
_INTERNAL_MODULES = frozenset(
    {
        "driftbuild.action",
        "driftbuild.adapter_action",
        "driftbuild.autotools",
        "driftbuild.bundle",
        "driftbuild.cargo",
        "driftbuild.conan",
        "driftbuild.opaque",
        "driftbuild.prebuilt",
        "driftbuild.provider_action",
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


def pip_command() -> tuple[str, ...]:
    """Return a command that invokes Drift's bundled pip runtime."""
    executable = sys.executable
    if getattr(sys, "frozen", False):
        return executable, _PIP_MARKER
    return executable, "-m", "pip"


def provider_command(root: Path, handler: str) -> tuple[str, ...]:
    """Return a command that invokes one project-owned provider handler."""
    executable = sys.executable
    if getattr(sys, "frozen", False):
        return executable, _PROVIDER_MARKER, str(root.resolve()), handler
    launcher = Path(sys.argv[0]).resolve()
    if launcher.suffix == ".pyz":
        return executable, str(launcher), _PROVIDER_MARKER, str(root.resolve()), handler
    return executable, "-m", "driftbuild.provider_action", "--root", str(root.resolve()), "--handler", handler


def _external_path(path: Path) -> None:
    value = str(path.resolve())
    if value not in sys.path:
        sys.path.insert(0, value)


def internal_dispatch(arguments: Sequence[str]) -> int | None:
    """Run a trusted helper request, or return None for a normal CLI invocation."""
    project_site = os.environ.get("DRIFT_PROJECT_SITE")
    if project_site:
        for value in reversed(project_site.split(os.pathsep)):
            if value:
                _external_path(Path(value))
    if arguments and arguments[0] == "-c":
        if len(arguments) < 2:
            raise ValueError("Python -c requires code")
        _external_path(Path.cwd())
        previous = sys.argv
        sys.argv = ["-c", *arguments[2:]]
        try:
            namespace = {"__name__": "__main__", "__file__": None, "__builtins__": __builtins__}
            exec(compile(arguments[1], "<string>", "exec"), namespace)
        finally:
            sys.argv = previous
        return 0
    if arguments and arguments[0] == "-m":
        if len(arguments) < 2:
            raise ValueError("Python -m requires a module")
        _external_path(Path.cwd())
        previous = sys.argv
        sys.argv = [arguments[1], *arguments[2:]]
        try:
            runpy.run_module(arguments[1], run_name="__main__", alter_sys=True)
        finally:
            sys.argv = previous
        return 0
    if arguments and arguments[0].casefold().endswith((".py", ".pyw")):
        script = Path(arguments[0]).resolve()
        previous = sys.argv
        sys.argv = [str(script), *arguments[1:]]
        _external_path(script.parent)
        try:
            runpy.run_path(str(script), run_name="__main__")
        finally:
            sys.argv = previous
        return 0
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
        from driftbuild.storage import tool_store_root
        from driftbuild.versions import CONAN_VERSION

        _external_path(tool_store_root() / "conan" / CONAN_VERSION / "site-packages")
        conan_main = cast(Callable[[list[str]], int | None], importlib.import_module("conan.cli.cli").main)
        return int(conan_main(list(arguments[1:])) or 0)
    if arguments and arguments[0] == _PROVIDER_MARKER:
        if len(arguments) < 3:
            raise ValueError("Provider action requires a root and handler")
        from driftbuild.provider_action import invoke

        return invoke(Path(arguments[1]), arguments[2], arguments[3:])
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
