"""Fallback adapters for conventional Make, B2, and SCons projects."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.model import (
    ActionSpec,
    BuildConfig,
    CompileInterface,
    Dependency,
    LinkInterface,
    PackageSpec,
    ProjectSpec,
    TargetRef,
    TargetSpec,
)
from driftbuild.package_cache import package_build_root
from driftbuild.process import run
from driftbuild.toolchain import toolchain_resolve


def _tool(adapter: str) -> str:
    names = {
        "make": ("make", "gmake"),
        "b2": ("b2", "bjam"),
        "scons": ("scons",),
    }[adapter]
    override = os.environ.get(f"DRIFT_{adapter.upper()}")
    selected = override or next((name for name in names if shutil.which(name) is not None), None)
    if selected is None:
        raise ConfigurationError(f"{adapter} package adapter requires a host {names[0]} executable")
    return selected


def project_import(
    source_root: Path,
    state_root: Path,
    config: BuildConfig,
    package: PackageSpec,
    adapter: str,
) -> ProjectSpec:
    """Expose a conventional staged install behind one explicit build action."""
    toolchain = toolchain_resolve(config, state_root.parent)
    build_root = package_build_root(source_root, package, config, adapter)
    install_root = build_root / "install"
    stamp = install_root / ".drift-installed"
    environment = dict(toolchain.environment)
    environment.update({"CC": toolchain.cc, "CXX": toolchain.cxx, "AR": toolchain.archiver})
    if config.sysroot is not None:
        environment["CFLAGS"] = f"--sysroot={config.sysroot}"
        environment["CXXFLAGS"] = f"--sysroot={config.sysroot}"
    library = package.name.removeprefix("lib")
    link_arguments = (f"{library}.lib",) if config.platform == "win32" else (f"-l{library}",)
    interface = Dependency(
        package.name,
        CompileInterface((source_root / "include", install_root / "include")),
        LinkInterface(library_dirs=(install_root / "lib", install_root / "lib64"), arguments=link_arguments),
    )
    action = ActionSpec(
        command=(
            sys.executable,
            "-m",
            "driftbuild.opaque",
            "--adapter",
            adapter,
            "--tool",
            _tool(adapter),
            "--source-root",
            str(source_root),
            "--build-root",
            str(build_root),
            "--install-root",
            str(install_root),
            *(value for option in package.options for value in ("--option", f"{option[0]}={option[1]}")),
        ),
        outputs=(stamp,),
        environment=environment,
        description=f"{adapter.upper()} {package.name}",
        pool="console",
        restat=True,
    )
    target = TargetSpec(
        package.name,
        "external_library",
        include_dirs=interface.compile.include_dirs,
        dependencies=(interface,),
        outputs=(stamp,),
        action=action,
    )
    return ProjectSpec(package.name, (target,), (TargetRef(package.name),))


def main() -> int:
    """Build and install an opaque upstream project in an isolated copy."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", choices=("make", "b2", "scons"), required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--option", action="append", default=[])
    arguments = parser.parse_args()
    work = arguments.build_root / "work"
    if not work.is_dir():
        shutil.copytree(arguments.source_root, work)
    jobs = str(os.cpu_count() or 1)
    options = tuple(arguments.option)
    if arguments.adapter == "make":
        run((arguments.tool, "-C", str(work), f"-j{jobs}", *options))
        run((arguments.tool, "-C", str(work), "install", f"PREFIX={arguments.install_root}", *options))
    elif arguments.adapter == "b2":
        run((arguments.tool, f"-j{jobs}", "install", f"--prefix={arguments.install_root}", *options), cwd=work)
    else:
        run((arguments.tool, "-C", str(work), f"-j{jobs}", *options))
        run((arguments.tool, "-C", str(work), "install", f"prefix={arguments.install_root}", *options))
    arguments.install_root.mkdir(parents=True, exist_ok=True)
    (arguments.install_root / ".drift-installed").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
