"""Fallback adapters for conventional Make, B2, and SCons projects."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.locking import cache_lock
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
from driftbuild.runtime import module_command
from driftbuild.toolchain import toolchain_resolve


def _strings(payload: object, name: str) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        return ()
    value = payload.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(f"drift-package.json field {name} must be an array of strings")
    return tuple(value)


def _interface(source_root: Path, install_root: Path, package: PackageSpec, config: BuildConfig) -> Dependency:
    manifest = source_root / "drift-package.json"
    library = package.name.removeprefix("lib")
    libraries = package.features or (library,)
    default_link = tuple(f"{name}.lib" if config.platform == "win32" else f"-l{name}" for name in libraries)
    if not manifest.is_file():
        return Dependency(
            package.name,
            CompileInterface((source_root / "include", install_root / "include")),
            LinkInterface(library_dirs=(install_root / "lib", install_root / "lib64"), arguments=default_link),
        )
    try:
        payload: object = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Cannot read {manifest}: {error}") from error
    if not isinstance(payload, dict):
        raise ConfigurationError("drift-package.json must contain an object")

    def path(value: str) -> Path:
        expanded = value.replace("${source}", str(source_root)).replace("${prefix}", str(install_root))
        candidate = Path(expanded)
        return candidate.resolve() if candidate.is_absolute() else (source_root / candidate).resolve()

    return Dependency(
        package.name,
        CompileInterface(
            tuple(path(value) for value in _strings(payload, "include_dirs")),
            _strings(payload, "defines"),
            _strings(payload, "compile_arguments"),
        ),
        LinkInterface(
            tuple(path(value) for value in _strings(payload, "libraries")),
            tuple(path(value) for value in _strings(payload, "library_dirs")),
            _strings(payload, "link_arguments") or default_link,
        ),
        tuple(path(value) for value in _strings(payload, "runtime_files")),
    )


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
    interface = _interface(source_root, install_root, package, config)
    produced = tuple(value for value in interface.link.libraries if isinstance(value, Path))
    runtime_outputs = tuple(value for value in interface.runtime_files if isinstance(value, Path))
    action = ActionSpec(
        command=(
            *module_command("driftbuild.opaque"),
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
        outputs=(*produced, *runtime_outputs, stamp),
        environment=environment,
        description=f"{adapter.upper()} {package.name}",
        pool="console",
        restat=True,
    )
    target = TargetSpec(
        package.name,
        "external_library",
        include_dirs=interface.compile.include_dirs,
        defines=interface.compile.defines,
        compile_arguments=interface.compile.arguments,
        dependencies=(interface,),
        runtime_files=interface.runtime_files,
        outputs=action.outputs,
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
    with cache_lock(arguments.build_root.with_suffix(".lock")):
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
