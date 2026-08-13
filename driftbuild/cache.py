"""Inspection and explicit cleanup for shared Drift caches."""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from driftbuild.errors import ExecutionError
from driftbuild.storage import drift_home

_CATEGORIES = ("sources", "binaries", "tools", "python", "conan", "vcpkg")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
        "python": home / "python",
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


def cache_export(destination: Path, categories: tuple[str, ...]) -> Path:
    """Export selected shared cache categories as a portable tar archive."""
    requested = _CATEGORIES if "all" in categories else categories
    unknown = sorted(set(requested) - set(_CATEGORIES))
    if unknown:
        raise ExecutionError(f"Unknown cache categories: {', '.join(unknown)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    with tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name in dict.fromkeys(requested):
            path = cache_paths()[name]
            if path.is_dir():
                for value in sorted(path.rglob("*")):
                    if value.is_symlink() or not value.is_file():
                        continue
                    relative = Path(name) / value.relative_to(path)
                    files[relative.as_posix()] = _sha256(value)
                    archive.add(value, arcname=relative.as_posix(), recursive=False)
        encoded = (json.dumps({"version": 1, "files": files}, indent=2, sort_keys=True) + "\n").encode()
        info = tarfile.TarInfo("drift-cache-manifest.json")
        info.size = len(encoded)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(encoded))
    return destination


def cache_import(source: Path, *, replace: bool = False) -> int:
    """Merge a previously exported cache archive into DRIFT_HOME."""
    if not source.is_file():
        raise ExecutionError(f"Cache archive does not exist: {source}")
    imported = 0
    with tempfile.TemporaryDirectory(prefix="drift-cache-import-") as temporary_text:
        temporary = Path(temporary_text)
        try:
            with tarfile.open(source, "r:gz") as archive:
                for member in archive.getmembers():
                    path = Path(member.name)
                    root_allowed = path.parts and (
                        path.parts[0] in _CATEGORIES or path.as_posix() == "drift-cache-manifest.json"
                    )
                    if path.is_absolute() or ".." in path.parts or not root_allowed:
                        raise ExecutionError(f"Unsafe cache archive member: {member.name}")
                    if member.issym() or member.islnk():
                        raise ExecutionError(f"Cache archives cannot contain links: {member.name}")
                archive.extractall(temporary, filter="data")
        except (OSError, tarfile.TarError) as error:
            raise ExecutionError(f"Cannot import cache archive {source}: {error}") from error
        manifest_path = temporary / "drift-cache-manifest.json"
        try:
            manifest: object = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ExecutionError(f"Cache archive has no valid integrity manifest: {error}") from error
        if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict):
            raise ExecutionError("Cache archive has an unsupported integrity manifest")
        expected = manifest["files"]
        if not all(isinstance(name, str) and isinstance(digest, str) for name, digest in expected.items()):
            raise ExecutionError("Cache archive integrity manifest is malformed")
        extracted = {
            path.relative_to(temporary).as_posix(): _sha256(path)
            for category in _CATEGORIES
            for path in (temporary / category).rglob("*")
            if path.is_file()
        }
        if extracted != expected:
            raise ExecutionError("Cache archive contents do not match its integrity manifest")
        for category in _CATEGORIES:
            category_root = temporary / category
            if not category_root.is_dir():
                continue
            for path in sorted(category_root.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(temporary)
                destination = cache_paths()[relative.parts[0]].joinpath(*relative.parts[1:])
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() and not replace and _sha256(destination) != _sha256(path):
                    raise ExecutionError(f"Cache import conflicts with existing file: {destination}")
                shutil.copy2(path, destination)
                imported += 1
    return imported


def cache_pull(url: str, *, replace: bool = False) -> int:
    """Download and import a remote cache archive over HTTPS or file URL."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("https", "file"):
        raise ExecutionError("Remote cache pull requires an HTTPS or file URL")
    if parsed.username is not None or parsed.password is not None:
        raise ExecutionError("Remote cache URLs cannot contain credentials; use DRIFT_CACHE_TOKEN")
    with tempfile.TemporaryDirectory(prefix="drift-cache-pull-") as temporary_text:
        archive = Path(temporary_text) / "cache.tar.gz"
        try:
            headers = {"User-Agent": "drift-build/0"}
            token = os.environ.get("DRIFT_CACHE_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        except (OSError, ValueError) as error:
            raise ExecutionError(f"Cannot pull remote cache {url}: {error}") from error
        return cache_import(archive, replace=replace)


def cache_push(url: str, categories: tuple[str, ...]) -> None:
    """Export and upload cache categories with HTTPS PUT or a file URL."""
    parsed = urllib.parse.urlparse(url)
    if parsed.username is not None or parsed.password is not None:
        raise ExecutionError("Remote cache URLs cannot contain credentials; use DRIFT_CACHE_TOKEN")
    with tempfile.TemporaryDirectory(prefix="drift-cache-push-") as temporary_text:
        archive = cache_export(Path(temporary_text) / "cache.tar.gz", categories)
        if parsed.scheme == "file":
            destination = Path(urllib.request.url2pathname(parsed.path))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive, destination)
            return
        if parsed.scheme != "https":
            raise ExecutionError("Remote cache push requires an HTTPS or file URL")
        if parsed.hostname is None:
            raise ExecutionError("Remote cache push URL has no host")
        headers = {"Content-Type": "application/gzip", "User-Agent": "drift-build/0"}
        token = os.environ.get("DRIFT_CACHE_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=300)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        try:
            connection.putrequest("PUT", target)
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.putheader("Content-Length", str(archive.stat().st_size))
            connection.endheaders()
            with archive.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    connection.send(chunk)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                detail = response.read(4096).decode("utf-8", errors="replace")
                raise ExecutionError(f"Remote cache rejected upload with HTTP {response.status}: {detail}")
            response.read()
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise ExecutionError(f"Cannot push remote cache {url}: {error}") from error
        finally:
            connection.close()


def size_render(size: int) -> str:
    """Render a byte count using compact binary units."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")
