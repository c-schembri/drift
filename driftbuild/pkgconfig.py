"""Resolve installed package interfaces through pkg-config or pkgconf."""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.model import CompileInterface, Dependency, LinkInterface
from driftbuild.process import run


def _executable() -> str:
    override = os.environ.get("DRIFT_PKG_CONFIG")
    selected = override or next((name for name in ("pkg-config", "pkgconf") if shutil.which(name)), None)
    if selected is None:
        raise ConfigurationError("pkg-config dependency requested but pkg-config/pkgconf is unavailable")
    return selected


def _arguments(value: str) -> list[str]:
    return shlex.split(value, posix=os.name != "nt")


def dependency_resolve(name: str, *, static: bool = False) -> Dependency:
    """Return the compile and link interface reported for one installed package."""
    command = [_executable()]
    if static:
        command.append("--static")
    cflags = _arguments(run((*command, "--cflags", name), capture=True).stdout)
    libs = _arguments(run((*command, "--libs", name), capture=True).stdout)
    include_dirs: list[Path] = []
    defines: list[str] = []
    compile_arguments: list[str] = []
    index = 0
    while index < len(cflags):
        value = cflags[index]
        if value == "-I" and index + 1 < len(cflags):
            index += 1
            include_dirs.append(Path(cflags[index]))
        elif value.startswith("-I"):
            include_dirs.append(Path(value[2:]))
        elif value.startswith("-D"):
            defines.append(value[2:])
        else:
            compile_arguments.append(value)
        index += 1
    library_dirs: list[Path] = []
    libraries: list[Path] = []
    link_arguments: list[str] = []
    index = 0
    while index < len(libs):
        value = libs[index]
        if value == "-L" and index + 1 < len(libs):
            index += 1
            library_dirs.append(Path(libs[index]))
        elif value.startswith("-L"):
            library_dirs.append(Path(value[2:]))
        elif Path(value).is_absolute() and Path(value).suffix:
            libraries.append(Path(value))
        else:
            link_arguments.append(value)
        index += 1
    return Dependency(
        name,
        CompileInterface(tuple(include_dirs), tuple(defines), tuple(compile_arguments)),
        LinkInterface(tuple(libraries), tuple(library_dirs), tuple(link_arguments)),
    )
