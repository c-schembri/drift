"""Import conventional Autotools projects through a staged install prefix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
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
    ProjectSpec,
    TargetRef,
    TargetSpec,
)
from driftbuild.process import run
from driftbuild.toolchain import Toolchain, toolchain_resolve

_SCHEMA_VERSION = 1


def _state_key(config: BuildConfig) -> str:
    values = (config.platform, config.architecture, config.compiler, config.build_type)
    return "-".join(re.sub(r"[^A-Za-z0-9_.-]", "_", value) for value in values)


def _tool(name: str, candidates: tuple[str, ...]) -> str:
    override = os.environ.get(f"DRIFT_{name.upper()}")
    selected = override or next((value for value in candidates if shutil.which(value) is not None), None)
    if selected is None:
        raise ConfigurationError(f"Autotools adapter requires {name}; set DRIFT_{name.upper()}")
    return selected


def _environment(toolchain: Toolchain) -> dict[str, str]:
    environment = dict(toolchain.environment)
    environment.update({"CC": toolchain.cc, "CXX": toolchain.cxx, "AR": toolchain.archiver})
    return environment


def _configure(
    source_root: Path,
    build_root: Path,
    install_root: Path,
    config: BuildConfig,
    shell: str,
    make: str,
    environment: dict[str, str],
) -> None:
    configure = source_root / "configure"
    fingerprint = {
        "schema": _SCHEMA_VERSION,
        "source": str(source_root.resolve()),
        "configure_sha256": hashlib.sha256(configure.read_bytes()).hexdigest(),
        "shell": shell,
        "make": make,
        "build_type": config.build_type,
        "architecture": config.architecture,
    }
    state_path = build_root / ".drift-import.json"
    cached = False
    if state_path.is_file() and (build_root / "Makefile").is_file():
        try:
            cached = json.loads(state_path.read_text(encoding="utf-8")) == fingerprint
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    if cached:
        return
    arguments = [shell, str(configure), f"--prefix={install_root}"]
    if config.build_type != "debug":
        environment = dict(environment)
        environment["CFLAGS"] = "-O2 -DNDEBUG"
        environment["CXXFLAGS"] = "-O2 -DNDEBUG"
    build_root.mkdir(parents=True, exist_ok=True)
    run(arguments, cwd=build_root, environment=environment, capture=True)
    temporary = state_path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(fingerprint, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, state_path)


def _package_flags(source_root: Path, package_name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    candidates = sorted((*source_root.rglob("*.pc.in"), *source_root.rglob("*.pc")))
    package_key = re.sub(r"[^a-z0-9]", "", package_name.casefold())
    candidates.sort(
        key=lambda path: (
            re.sub(r"[^a-z0-9]", "", path.name.split(".pc", 1)[0].casefold()) != package_key,
            len(path.parts),
            str(path),
        )
    )
    if not candidates:
        library = re.sub(r"^lib", "", package_name, flags=re.IGNORECASE)
        return (), (f"-l{library}",)
    try:
        text = candidates[0].read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return (), (f"-l{package_name}",)
    cflags = next((line.split(":", 1)[1] for line in text.splitlines() if line.startswith("Cflags:")), "")
    libs = next((line.split(":", 1)[1] for line in text.splitlines() if line.startswith("Libs:")), "")
    definitions = tuple(value[2:] for value in shlex.split(cflags) if value.startswith("-D"))
    link_arguments = tuple(
        value for value in shlex.split(libs) if value.startswith("-l") or value.startswith("-Wl,")
    )
    if not link_arguments:
        library = re.sub(r"^lib", "", package_name, flags=re.IGNORECASE)
        link_arguments = (f"-l{library}",)
    return definitions, link_arguments


def project_import(source_root: Path, state_root: Path, config: BuildConfig, package_name: str) -> ProjectSpec:
    """Configure an Autotools project and expose its conventional install interface."""
    if config.platform == "win32":
        raise ConfigurationError("Autotools package import currently requires a POSIX host")
    tool_root = state_root.parent
    toolchain = toolchain_resolve(config, tool_root)
    shell = _tool("sh", ("sh",))
    make = _tool("make", ("gmake", "make"))
    source_key = hashlib.sha256(str(source_root.resolve()).encode("utf-8")).hexdigest()[:16]
    build_root = state_root / package_name / _state_key(config) / "autotools" / source_key
    install_root = build_root / "install"
    environment = _environment(toolchain)
    _configure(source_root, build_root, install_root, config, shell, make, environment)
    stamp = install_root / ".drift-installed"
    definitions, link_arguments = _package_flags(source_root, package_name)
    include_dirs = [source_root, build_root, install_root / "include"]
    if (source_root / "include").is_dir():
        include_dirs.append(source_root / "include")
    interface = Dependency(
        package_name,
        CompileInterface(tuple(include_dirs), definitions),
        LinkInterface(
            library_dirs=(install_root / "lib", install_root / "lib64"),
            arguments=link_arguments,
        ),
    )
    action = ActionSpec(
        command=(
            sys.executable,
            "-m",
            "driftbuild.autotools",
            "--make",
            make,
            "--build-root",
            str(build_root),
            "--stamp",
            str(stamp),
        ),
        outputs=(stamp,),
        environment=environment,
        description=f"AUTOTOOLS {package_name}",
        pool="console",
        restat=True,
    )
    target = TargetSpec(
        package_name,
        "external_library",
        include_dirs=tuple(include_dirs),
        dependencies=(interface,),
        outputs=(stamp,),
        action=action,
    )
    return ProjectSpec(package_name, (target,), (TargetRef(package_name),))


def main() -> int:
    """Build and stage one configured Autotools project for a Ninja action."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--make", required=True)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--stamp", type=Path, required=True)
    arguments = parser.parse_args()
    run((arguments.make, "-C", str(arguments.build_root), f"-j{os.cpu_count() or 1}"))
    run((arguments.make, "-C", str(arguments.build_root), "install"))
    arguments.stamp.parent.mkdir(parents=True, exist_ok=True)
    arguments.stamp.touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
