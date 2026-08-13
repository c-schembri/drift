"""Import upstream CMake codemodels as externally built Drift targets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, cast

from driftbuild.bootstrap import cmake_resolve, ninja_resolve
from driftbuild.errors import ConfigurationError
from driftbuild.locking import cache_lock
from driftbuild.model import (
    ActionSpec,
    BuildConfig,
    PackageSpec,
    ProjectSpec,
    TargetDependency,
    TargetKind,
    TargetRef,
    TargetSpec,
)
from driftbuild.package_cache import package_build_root
from driftbuild.process import run
from driftbuild.toolchain import toolchain_resolve

_SCHEMA_VERSION = 1
_TARGET_KINDS = {"STATIC_LIBRARY", "SHARED_LIBRARY", "MODULE_LIBRARY", "EXECUTABLE"}


def _configuration_name(config: BuildConfig) -> str:
    return "Debug" if config.build_type == "debug" else "Release"


def _json_read(path: Path) -> dict[str, Any]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Cannot read CMake File API response {path}: {error}") from error
    if not isinstance(value, dict):
        raise ConfigurationError(f"CMake File API response is not an object: {path}")
    return cast(dict[str, Any], value)


def _reply_index(reply_root: Path) -> dict[str, Any]:
    indexes = sorted(reply_root.glob("index-*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not indexes:
        raise ConfigurationError("CMake did not produce a File API index")
    return _json_read(indexes[0])


def _codemodel_file(index: dict[str, Any]) -> str:
    objects = index.get("objects")
    if not isinstance(objects, list):
        raise ConfigurationError("CMake File API index has no object list")
    for value in objects:
        if isinstance(value, dict) and value.get("kind") == "codemodel" and isinstance(value.get("jsonFile"), str):
            return cast(str, value["jsonFile"])
    raise ConfigurationError("CMake did not answer the codemodel-v2 query")


def _configure(
    source_root: Path,
    build_root: Path,
    config: BuildConfig,
    cmake: str,
    ninja: str,
    environment: dict[str, str],
    package: PackageSpec | str,
) -> tuple[Path, dict[str, Any]]:
    query = build_root / ".cmake" / "api" / "v1" / "query" / "codemodel-v2"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.touch()
    cmake_path = Path(cmake).resolve()
    cmake_stat = cmake_path.stat()
    ninja_path = Path(ninja).resolve()
    ninja_stat = ninja_path.stat()
    fingerprint = {
        "schema": _SCHEMA_VERSION,
        "source": str(source_root.resolve()),
        "cmake": str(cmake_path),
        "cmake_mtime_ns": cmake_stat.st_mtime_ns,
        "cmake_size": cmake_stat.st_size,
        "ninja": str(ninja_path),
        "ninja_mtime_ns": ninja_stat.st_mtime_ns,
        "ninja_size": ninja_stat.st_size,
        "configuration": _configuration_name(config),
        "architecture": config.architecture,
        "target": config.target,
        "sysroot": str(config.sysroot) if config.sysroot is not None else None,
        "toolchain_file": str(config.toolchain_file) if config.toolchain_file is not None else None,
        "options": dict(package.options) if isinstance(package, PackageSpec) else {},
        "features": list(package.features) if isinstance(package, PackageSpec) else [],
    }
    state_path = build_root / ".drift-import.json"
    reply_root = build_root / ".cmake" / "api" / "v1" / "reply"
    cached = False
    if state_path.is_file() and reply_root.is_dir():
        try:
            cached = json.loads(state_path.read_text(encoding="utf-8")) == fingerprint
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    if not cached:
        arguments = [
            cmake,
            "-S",
            str(source_root),
            "-B",
            str(build_root),
            "-G",
            "Ninja",
            f"-DCMAKE_MAKE_PROGRAM={ninja}",
            f"-DCMAKE_BUILD_TYPE={_configuration_name(config)}",
            "-DBUILD_TESTING=OFF",
        ]
        if isinstance(package, PackageSpec):
            arguments.extend(f"-D{name}={value}" for name, value in package.options)
            arguments.extend(f"-D{feature}=ON" for feature in package.features)
        if config.target is not None:
            arguments.extend(
                (f"-DCMAKE_C_COMPILER_TARGET={config.target}", f"-DCMAKE_CXX_COMPILER_TARGET={config.target}")
            )
        if config.sysroot is not None:
            arguments.append(f"-DCMAKE_SYSROOT={config.sysroot}")
        if config.toolchain_file is not None and config.toolchain_file.suffix.casefold() == ".cmake":
            arguments.append(f"-DCMAKE_TOOLCHAIN_FILE={config.toolchain_file}")
        run(arguments, cwd=source_root, environment=environment, capture=True)
        temporary = state_path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(fingerprint, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, state_path)
    index = _reply_index(reply_root)
    return reply_root, index


def _selected_configuration(codemodel: dict[str, Any], name: str) -> dict[str, Any]:
    configurations = codemodel.get("configurations")
    if not isinstance(configurations, list) or not configurations:
        raise ConfigurationError("CMake codemodel has no configurations")
    for value in configurations:
        if isinstance(value, dict) and str(value.get("name", "")).casefold() == name.casefold():
            return cast(dict[str, Any], value)
    first = configurations[0]
    if not isinstance(first, dict):
        raise ConfigurationError("CMake codemodel contains an invalid configuration")
    return cast(dict[str, Any], first)


def _target_includes(target: dict[str, Any]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    groups = target.get("compileGroups", [])
    if not isinstance(groups, list):
        return ()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("includes"), list):
            continue
        for include in group["includes"]:
            if not isinstance(include, dict) or not isinstance(include.get("path"), str):
                continue
            path = Path(include["path"]).resolve()
            key = str(path).casefold()
            if key not in seen:
                seen.add(key)
                result.append(path)
    return tuple(result)


def _target_link_arguments(target: dict[str, Any]) -> tuple[str, ...]:
    link = target.get("link")
    if not isinstance(link, dict) or not isinstance(link.get("commandFragments"), list):
        return ()
    result: list[str] = []
    fragments: list[str] = []
    for value in link["commandFragments"]:
        if not isinstance(value, dict) or not isinstance(value.get("fragment"), str):
            continue
        if value.get("role") in ("flags", "libraries"):
            fragments.extend(shlex.split(value["fragment"], posix=os.name != "nt"))
    index = 0
    while index < len(fragments):
        value = fragments[index]
        if value == "-framework" and index + 1 < len(fragments):
            result.extend((value, fragments[index + 1]))
            index += 2
            continue
        if value.startswith("-l") or value in ("-pthread", "-pthreads") or value.startswith("/DEFAULTLIB:"):
            result.append(value)
        index += 1
    return tuple(result)


def _target_outputs(target: dict[str, Any], build_root: Path) -> tuple[Path, ...]:
    artifacts = target.get("artifacts", [])
    if not isinstance(artifacts, list):
        return ()
    result: list[Path] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            continue
        path = Path(artifact["path"])
        result.append(path.resolve() if path.is_absolute() else (build_root / path).resolve())
    return tuple(result)


def _default_target(package_name: str, targets: tuple[TargetSpec, ...]) -> TargetRef | None:
    libraries = [target for target in targets if target.kind == "external_library"]
    if not libraries:
        return None
    if len(libraries) == 1:
        return TargetRef(libraries[0].name)
    package_key = re.sub(r"[^a-z0-9]", "", package_name.casefold())

    def score(target: TargetSpec) -> tuple[int, int, int, str]:
        target_key = re.sub(r"[^a-z0-9]", "", target.name.casefold())
        match = 0 if target_key == package_key else 1 if target_key.startswith(package_key) else 2
        linkage = 0 if "shared" in target.name.casefold() else 1 if "static" in target.name.casefold() else 2
        return match, linkage, len(target.name), target.name.casefold()

    selected = min(libraries, key=score)
    return TargetRef(selected.name) if score(selected)[0] < 2 else None


def project_import(
    source_root: Path,
    state_root: Path,
    config: BuildConfig,
    package: PackageSpec | str,
) -> ProjectSpec:
    """Configure CMake once, read its File API graph, and expose buildable targets."""
    tool_root = state_root.parent
    cmake = str(cmake_resolve(tool_root))
    ninja = str(ninja_resolve(tool_root))
    environment = dict(toolchain_resolve(config, tool_root).environment)
    package_name = package.name if isinstance(package, PackageSpec) else package
    build_root = package_build_root(source_root, package, config, "cmake")
    with cache_lock(build_root.with_suffix(".lock")):
        reply_root, index = _configure(source_root, build_root, config, cmake, ninja, environment, package)
    codemodel = _json_read(reply_root / _codemodel_file(index))
    configuration = _selected_configuration(codemodel, _configuration_name(config))
    entries = configuration.get("targets")
    if not isinstance(entries, list):
        raise ConfigurationError("CMake codemodel configuration has no targets")

    descriptions: dict[str, dict[str, Any]] = {}
    names_by_id: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id")
        name = entry.get("name")
        response = entry.get("jsonFile")
        if not isinstance(identifier, str) or not isinstance(name, str) or not isinstance(response, str):
            continue
        description = _json_read(reply_root / response)
        if description.get("type") not in _TARGET_KINDS:
            continue
        outputs = _target_outputs(description, build_root)
        if not outputs:
            continue
        descriptions[identifier] = description
        names_by_id[identifier] = name

    targets: list[TargetSpec] = []
    configuration_name = _configuration_name(config)
    for identifier, description in descriptions.items():
        name = names_by_id[identifier]
        dependencies: list[TargetDependency] = []
        raw_dependencies = description.get("dependencies", [])
        if isinstance(raw_dependencies, list):
            for dependency in raw_dependencies:
                if not isinstance(dependency, dict) or not isinstance(dependency.get("id"), str):
                    continue
                dependency_name = names_by_id.get(dependency["id"])
                if dependency_name is not None:
                    dependencies.append(TargetDependency(TargetRef(dependency_name), "public"))
        outputs = _target_outputs(description, build_root)
        stamp = build_root / ".drift-built" / f"{hashlib.sha256(identifier.encode()).hexdigest()}.stamp"
        action = ActionSpec(
            command=(
                sys.executable,
                "-m",
                "driftbuild.adapter_action",
                "--stamp",
                str(stamp),
                "--lock",
                str(build_root.with_suffix(".lock")),
                "--",
                cmake,
                "--build",
                str(build_root),
                "--config",
                configuration_name,
                "--target",
                name,
                "--parallel",
            ),
            outputs=(*outputs, stamp),
            implicit_inputs=(build_root / ".drift-import.json", build_root / "CMakeCache.txt"),
            description=f"CMAKE {package_name}:{name}",
            pool="console",
            restat=True,
        )
        kind: TargetKind = "custom" if description.get("type") == "EXECUTABLE" else "external_library"
        targets.append(
            TargetSpec(
                name=name,
                kind=kind,
                include_dirs=_target_includes(description),
                link_arguments=_target_link_arguments(description),
                dependencies=tuple(dependencies),
                outputs=action.outputs,
                action=action,
            )
        )
    result = tuple(targets)
    if not result:
        raise ConfigurationError(f"CMake package {package_name} exposes no buildable native targets")
    selected = _default_target(package_name, result)
    return ProjectSpec(package_name, result, (selected,) if selected is not None else ())
