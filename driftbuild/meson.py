"""Import configured Meson introspection data as externally built targets."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, cast

from driftbuild.bootstrap import meson_command, ninja_resolve
from driftbuild.errors import ConfigurationError
from driftbuild.model import (
    ActionSpec,
    BuildConfig,
    CompileInterface,
    Dependency,
    LinkInterface,
    ProjectSpec,
    TargetDependency,
    TargetKind,
    TargetRef,
    TargetSpec,
)
from driftbuild.process import run
from driftbuild.toolchain import Toolchain, toolchain_resolve

_SCHEMA_VERSION = 1
_TARGET_KINDS: dict[str, TargetKind] = {
    "executable": "custom",
    "static library": "external_library",
    "shared library": "external_library",
    "shared module": "external_library",
}


def _state_key(config: BuildConfig) -> str:
    values = (config.platform, config.architecture, config.compiler, config.build_type)
    return "-".join(re.sub(r"[^A-Za-z0-9_.-]", "_", value) for value in values)


def _json_read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Cannot read Meson introspection response {path}: {error}") from error


def _environment(toolchain: Toolchain) -> dict[str, str]:
    environment = dict(toolchain.environment)
    environment.update({"CC": toolchain.cc, "CXX": toolchain.cxx, "AR": toolchain.archiver})
    return environment


def _configure(
    source_root: Path,
    build_root: Path,
    install_root: Path,
    config: BuildConfig,
    meson: tuple[str, ...],
    ninja: Path,
    environment: dict[str, str],
) -> None:
    entry = Path(meson[-1]).resolve()
    fingerprint = {
        "schema": _SCHEMA_VERSION,
        "source": str(source_root.resolve()),
        "meson": list(meson),
        "meson_mtime_ns": entry.stat().st_mtime_ns,
        "ninja": str(ninja.resolve()),
        "ninja_mtime_ns": ninja.stat().st_mtime_ns,
        "build_type": config.build_type,
        "architecture": config.architecture,
    }
    state_path = build_root / ".drift-import.json"
    introspection = build_root / "meson-info" / "intro-targets.json"
    cached = False
    if state_path.is_file() and introspection.is_file():
        try:
            cached = json.loads(state_path.read_text(encoding="utf-8")) == fingerprint
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    if cached:
        return
    arguments = (
        *meson,
        "setup",
        str(build_root),
        str(source_root),
        "--backend=ninja",
        "--wrap-mode=nodownload",
        f"--buildtype={'debug' if config.build_type == 'debug' else 'release'}",
        f"--prefix={install_root}",
    )
    run(arguments, cwd=source_root, environment=environment, capture=True)
    temporary = state_path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(fingerprint, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, state_path)


def _include_dirs(target: dict[str, Any]) -> tuple[Path, ...]:
    includes: list[Path] = []
    seen_includes: set[str] = set()
    for source_group in target.get("target_sources", []):
        if not isinstance(source_group, dict):
            continue
        parameters = source_group.get("parameters", [])
        if not isinstance(parameters, list):
            continue
        index = 0
        while index < len(parameters):
            value = parameters[index]
            if not isinstance(value, str):
                index += 1
                continue
            include: str | None = None
            if value == "-I" and index + 1 < len(parameters) and isinstance(parameters[index + 1], str):
                include = parameters[index + 1]
                index += 1
            elif value.startswith("-I") and len(value) > 2:
                include = value[2:]
            elif value.startswith("/I") and len(value) > 2:
                include = value[2:].strip('"')
            if include is not None:
                path = Path(include).resolve()
                key = str(path).casefold()
                if key not in seen_includes:
                    seen_includes.add(key)
                    includes.append(path)
            index += 1
    return tuple(includes)


def _external_dependencies(build_root: Path) -> dict[str, Dependency]:
    payload = _json_read(build_root / "meson-info" / "intro-dependencies.json")
    if not isinstance(payload, list):
        return {}
    result: dict[str, Dependency] = {}
    for value in payload:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            continue
        raw_compile_arguments = value.get("compile_args")
        raw_link_arguments = value.get("link_args")
        compile_arguments = (
            tuple(item for item in raw_compile_arguments if isinstance(item, str))
            if isinstance(raw_compile_arguments, list)
            else ()
        )
        link_arguments = (
            tuple(item for item in raw_link_arguments if isinstance(item, str))
            if isinstance(raw_link_arguments, list)
            else ()
        )
        result[value["name"]] = Dependency(
            value["name"],
            CompileInterface(arguments=compile_arguments),
            LinkInterface(arguments=link_arguments),
        )
    return result


def _default_target(package_name: str, targets: tuple[TargetSpec, ...]) -> TargetRef | None:
    libraries = [target for target in targets if target.kind == "external_library"]
    if len(libraries) == 1:
        return TargetRef(libraries[0].name)
    package_key = re.sub(r"[^a-z0-9]", "", package_name.casefold())
    matches = [target for target in libraries if re.sub(r"[^a-z0-9]", "", target.name.casefold()) == package_key]
    return TargetRef(matches[0].name) if len(matches) == 1 else None


def project_import(source_root: Path, state_root: Path, config: BuildConfig, package_name: str) -> ProjectSpec:
    """Configure Meson once and expose its native target graph to Drift."""
    tool_root = state_root.parent
    meson = meson_command(tool_root)
    ninja = ninja_resolve(tool_root)
    toolchain = toolchain_resolve(config, tool_root)
    source_key = hashlib.sha256(str(source_root.resolve()).encode("utf-8")).hexdigest()[:16]
    build_root = state_root / package_name / _state_key(config) / "meson" / source_key
    install_root = build_root / "install"
    _configure(source_root, build_root, install_root, config, meson, ninja, _environment(toolchain))
    payload = _json_read(build_root / "meson-info" / "intro-targets.json")
    if not isinstance(payload, list):
        raise ConfigurationError("Meson introspection target response is not an array")
    external = _external_dependencies(build_root)
    descriptions = [cast(dict[str, Any], value) for value in payload if isinstance(value, dict)]
    names_by_id = {
        value["id"]: value["name"]
        for value in descriptions
        if isinstance(value.get("id"), str)
        and isinstance(value.get("name"), str)
        and value.get("type") in _TARGET_KINDS
    }
    targets: list[TargetSpec] = []
    for value in descriptions:
        identifier = value.get("id")
        name = value.get("name")
        target_type = value.get("type")
        filenames = value.get("filename")
        if (
            not isinstance(identifier, str)
            or not isinstance(name, str)
            or target_type not in _TARGET_KINDS
            or not isinstance(filenames, list)
        ):
            continue
        outputs = tuple(
            path.resolve() if path.is_absolute() else (build_root / path).resolve()
            for item in filenames
            if isinstance(item, str)
            for path in (Path(item),)
        )
        if not outputs:
            continue
        includes = _include_dirs(value)
        dependencies: list[Dependency | TargetDependency] = []
        raw_target_dependencies = value.get("depends")
        target_dependencies = raw_target_dependencies if isinstance(raw_target_dependencies, list) else []
        for dependency_id in target_dependencies:
            dependency_name = names_by_id.get(dependency_id)
            if dependency_name is not None:
                dependencies.append(TargetDependency(TargetRef(dependency_name), "public"))
        raw_external_dependencies = value.get("dependencies")
        external_dependencies = raw_external_dependencies if isinstance(raw_external_dependencies, list) else []
        for dependency_name in external_dependencies:
            dependency = external.get(dependency_name)
            if dependency is not None:
                dependencies.append(dependency)
        build_outputs = tuple(str(output.relative_to(build_root)) for output in outputs)
        action = ActionSpec(
            command=(str(ninja), "-C", str(build_root), *build_outputs),
            outputs=outputs,
            environment=_environment(toolchain),
            description=f"MESON {package_name}:{name}",
            pool="console",
            restat=True,
        )
        targets.append(
            TargetSpec(
                name,
                _TARGET_KINDS[target_type],
                include_dirs=includes,
                dependencies=tuple(dependencies),
                outputs=outputs,
                action=action,
            )
        )
    result = tuple(targets)
    if not result:
        raise ConfigurationError(f"Meson package {package_name} exposes no buildable native targets")
    selected = _default_target(package_name, result)
    return ProjectSpec(package_name, result, (selected,) if selected is not None else ())
