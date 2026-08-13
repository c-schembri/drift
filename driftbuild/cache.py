"""Inspection and explicit cleanup for shared Drift caches."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from driftbuild.errors import ExecutionError
from driftbuild.storage import drift_home

_CATEGORIES = ("sources", "binaries", "tools", "conan", "vcpkg")


@dataclass(frozen=True)
class CacheEntry:
    """Size and file count for one shared cache category."""

    name: str
    path: Path
    bytes: int
    files: int


def cache_paths() -> dict[str, Path]:
    """Return every user-manageable shared cache path."""
    home = drift_home()
    return {
        "sources": home / "store",
        "binaries": home / "binaries",
        "tools": home / "tools",
        "conan": home / "conan",
        "vcpkg": home / "vcpkg",
    }


def _measure(path: Path) -> tuple[int, int]:
    total = 0
    files = 0
    if not path.is_dir():
        return total, files
    for value in path.rglob("*"):
        if value.is_symlink() or not value.is_file():
            continue
        try:
            total += value.stat().st_size
            files += 1
        except OSError:
            continue
    return total, files


def cache_status() -> tuple[CacheEntry, ...]:
    """Measure all shared cache categories without changing them."""
    return tuple(CacheEntry(name, path, *_measure(path)) for name, path in cache_paths().items())


def cache_clean(categories: tuple[str, ...], *, confirmed: bool) -> tuple[CacheEntry, ...]:
    """Remove explicitly selected shared cache categories."""
    if not confirmed:
        raise ExecutionError("Shared cache cleanup requires --yes")
    requested = _CATEGORIES if "all" in categories else categories
    unknown = sorted(set(requested) - set(_CATEGORIES))
    if unknown:
        raise ExecutionError(f"Unknown cache categories: {', '.join(unknown)}")
    paths = cache_paths()
    home = drift_home().resolve()
    removed: list[CacheEntry] = []
    for name in dict.fromkeys(requested):
        path = paths[name].resolve()
        try:
            path.relative_to(home)
        except ValueError as error:
            raise ExecutionError(f"Unsafe cache path: {path}") from error
        if path == home:
            raise ExecutionError(f"Refusing to remove the Drift home directly: {path}")
        size, files = _measure(path)
        if path.is_dir():
            shutil.rmtree(path)
        removed.append(CacheEntry(name, path, size, files))
    return tuple(removed)


def size_render(size: int) -> str:
    """Render a byte count using compact binary units."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")
