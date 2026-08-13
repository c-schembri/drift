"""Explicit local SDK materialisation from descriptor file selections."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from driftbuild.errors import ConfigurationError
from driftbuild.model import LocalSdkSpec
from driftbuild.provider import copy_file, remove_tree, remove_tree_retry


def _patterns(payload: dict[str, Any], name: str) -> tuple[str, ...]:
    value = payload.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigurationError(f"Local SDK materialize field {name} must be an array of strings")
    for pattern in value:
        path = Path(pattern)
        if path.is_absolute() or ".." in path.parts:
            raise ConfigurationError(f"Local SDK materialize pattern escapes its root: {pattern}")
    return tuple(value)


def _has_symlink_ancestor(root: Path, path: Path) -> bool:
    current = root
    for part in path.relative_to(root).parts[:-1]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _files(root: Path, patterns: tuple[str, ...], *, required: bool) -> tuple[Path, ...]:
    found: dict[str, Path] = {}
    for pattern in patterns:
        matches = tuple(root.glob(pattern))
        files = tuple(
            child
            for match in matches
            for child in ((match,) if match.is_file() else match.rglob("*") if match.is_dir() else ())
            if child.is_file() and not child.is_symlink() and not _has_symlink_ancestor(root, child)
        )
        if required and not files:
            raise ConfigurationError(f"Local SDK materialize pattern matched no files: {root / pattern}")
        for path in files:
            relative = path.relative_to(root)
            key = relative.as_posix().casefold()
            previous = found.get(key)
            if previous is not None and previous != path:
                raise ConfigurationError(f"Local SDK contains case-colliding paths: {previous} and {path}")
            found[key] = path
    return tuple(sorted(found.values(), key=lambda path: path.relative_to(root).as_posix().casefold()))


def sdk_materialize(spec: LocalSdkSpec) -> int:
    """Replace one managed destination with the descriptor-selected SDK files."""
    try:
        payload = json.loads(spec.descriptor.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Cannot read local SDK descriptor {spec.descriptor}: {error}") from error
    materialize = payload.get("materialize") if isinstance(payload, dict) else None
    if not isinstance(materialize, dict):
        raise ConfigurationError(f"Local SDK {spec.name} descriptor has no materialize recipe")
    required = _files(spec.source, _patterns(materialize, "required"), required=True)
    optional = _files(spec.source, _patterns(materialize, "optional"), required=False)
    files = tuple(dict.fromkeys((*required, *optional)))
    if spec.source.resolve() == spec.destination.resolve():
        return len(files)

    temporary = spec.destination.with_name(f".{spec.destination.name}.drift-{os.getpid()}")
    backup = spec.destination.with_name(f".{spec.destination.name}.drift-backup-{os.getpid()}")
    if temporary.exists() or backup.exists():
        raise ConfigurationError(f"Local SDK staging paths already exist beside {spec.destination}")
    temporary.mkdir(parents=True)
    try:
        for source in files:
            copy_file(source, temporary / source.relative_to(spec.source))
        if spec.destination.exists():
            os.replace(spec.destination, backup)
        os.replace(temporary, spec.destination)
        if backup.exists():
            remove_tree_retry(backup)
    except Exception:
        if temporary.exists():
            remove_tree(temporary, ignore_errors=True)
        if backup.exists() and not spec.destination.exists():
            os.replace(backup, spec.destination)
        raise
    return len(files)


def sdks_materialize(specs: tuple[LocalSdkSpec, ...], names: tuple[str, ...] = ()) -> tuple[tuple[str, int], ...]:
    """Materialise selected SDKs in declaration order."""
    available = {spec.name: spec for spec in specs}
    selected = names or tuple(available)
    unknown = set(selected) - set(available)
    if unknown:
        raise ConfigurationError(f"Unknown local SDKs: {', '.join(sorted(unknown))}")
    return tuple((name, sdk_materialize(available[name])) for name in selected)
