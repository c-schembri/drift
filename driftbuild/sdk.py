"""Manifest-described local SDK interfaces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from driftbuild.errors import ConfigurationError
from driftbuild.model import BuildConfig, CompileInterface, Dependency, LinkInterface

_FIELDS = frozenset(
    {
        "include_dirs",
        "defines",
        "compile_arguments",
        "libraries",
        "optional_libraries",
        "library_dirs",
        "link_arguments",
        "runtime_files",
        "optional_runtime_files",
        "runtime_globs",
    }
)


def _strings(payload: dict[str, Any], name: str) -> tuple[str, ...]:
    value = payload.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(f"Local SDK field {name} must be an array of strings")
    return tuple(value)


def _matches(when: object, config: BuildConfig) -> bool:
    if not isinstance(when, dict):
        raise ConfigurationError("Local SDK variant when must be an object")
    known = {
        "platform": config.platform,
        "architecture": config.architecture,
        "compiler": config.compiler,
        "build_type": config.build_type,
    }
    for name, expected in when.items():
        if not isinstance(expected, str):
            raise ConfigurationError(f"Local SDK variant selector {name} must be a string")
        if name not in known and name not in config.values:
            raise ConfigurationError(f"Local SDK variant references unknown selector: {name}")
        actual = config.values.get(name) if name not in known else known[name]
        if actual != expected:
            return False
    return True


def local_sdk_load(
    name: str,
    root: Path,
    descriptor: Path,
    project_root: Path,
    config: BuildConfig,
) -> Dependency:
    """Load one local SDK interface and validate every required path."""
    try:
        payload: object = json.loads(descriptor.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Cannot read local SDK descriptor {descriptor}: {error}") from error
    if not isinstance(payload, dict):
        raise ConfigurationError(f"Local SDK descriptor {descriptor} must contain an object")
    unknown = sorted(set(payload) - _FIELDS - {"variants"})
    if unknown:
        raise ConfigurationError(f"Local SDK descriptor has unknown fields: {', '.join(unknown)}")
    for field in _FIELDS:
        _strings(payload, field)
    merged: dict[str, Any] = {key: value for key, value in payload.items() if key != "variants"}
    variants = payload.get("variants", [])
    if not isinstance(variants, list) or not all(isinstance(item, dict) for item in variants):
        raise ConfigurationError("Local SDK variants must be an array of objects")
    for variant in variants:
        unknown = sorted(set(variant) - _FIELDS - {"when"})
        if unknown:
            raise ConfigurationError(f"Local SDK variant has unknown fields: {', '.join(unknown)}")
        if not _matches(variant.get("when", {}), config):
            continue
        for key, value in variant.items():
            if key == "when":
                continue
            if not isinstance(value, list):
                raise ConfigurationError(f"Local SDK variant field {key} must be an array")
            merged[key] = [*merged.get(key, []), *value]

    replacements = {
        "${root}": str(root),
        "${project}": str(project_root),
        "${platform}": config.platform,
        "${architecture}": config.architecture,
        "${build_type}": config.build_type,
    }

    def expand(value: str) -> str:
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
        for option, selected in config.values.items():
            value = value.replace("${option:" + option + "}", selected)
        if "${" in value:
            raise ConfigurationError(f"Local SDK {name} contains an unresolved placeholder: {value}")
        return value

    def path(value: str, *, required: bool = True) -> Path:
        candidate = Path(expand(value))
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        if required and not resolved.exists():
            raise ConfigurationError(f"Local SDK {name} path does not exist: {resolved}")
        return resolved

    include_dirs = tuple(path(value) for value in _strings(merged, "include_dirs"))
    library_dirs = tuple(path(value) for value in _strings(merged, "library_dirs"))
    libraries = [path(value) for value in _strings(merged, "libraries")]
    runtime_files = [path(value) for value in _strings(merged, "runtime_files")]
    libraries.extend(
        candidate
        for value in _strings(merged, "optional_libraries")
        for candidate in (path(value, required=False),)
        if candidate.is_file()
    )
    runtime_files.extend(
        candidate
        for value in _strings(merged, "optional_runtime_files")
        for candidate in (path(value, required=False),)
        if candidate.is_file()
    )
    for pattern in _strings(merged, "runtime_globs"):
        matches = tuple(sorted(root.glob(expand(pattern))))
        if not matches:
            raise ConfigurationError(f"Local SDK {name} runtime glob matched no files: {pattern}")
        runtime_files.extend(match.resolve() for match in matches if match.is_file())
    return Dependency(
        name,
        CompileInterface(include_dirs, _strings(merged, "defines"), _strings(merged, "compile_arguments")),
        LinkInterface(
            tuple(dict.fromkeys(libraries)),
            library_dirs,
            tuple(expand(value) for value in _strings(merged, "link_arguments")),
        ),
        tuple(dict.fromkeys(runtime_files)),
        root,
    )
