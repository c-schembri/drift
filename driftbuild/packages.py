"""Locked package fetching, storage, and target graph composition."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from driftbuild.errors import ConfigurationError
from driftbuild.locking import cache_lock
from driftbuild.model import (
    ArchiveSource,
    Artifact,
    BuildConfig,
    BuildInput,
    CompileInterface,
    Dependency,
    Deployment,
    GitSource,
    LinkInterface,
    MsbuildProject,
    PackageSpec,
    PackageTargetRef,
    ProjectSpec,
    TargetDependency,
    TargetRef,
    TargetSpec,
    VcpkgSource,
)
from driftbuild.process import run
from driftbuild.project import ProjectApi, project_load
from driftbuild.storage import drift_home

LOCK_VERSION = 4
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
_MAX_ARCHIVE_FILES = 100_000
_EXTRACTED_MTIME = 315532800


@dataclass(frozen=True)
class LockedPackage:
    """One exact package source recorded in drift.lock."""

    name: str
    request_sha256: str
    content_sha256: str
    source: ArchiveSource | GitSource | VcpkgSource
    overlay: str | None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    scope: str = ""


@dataclass(frozen=True)
class PackageLock:
    """Complete deterministic package lock state."""

    packages: tuple[LockedPackage, ...]


def package_store_root() -> Path:
    """Return the shared Drift content store location."""
    return drift_home() / "store"


def _source_payload(source: ArchiveSource | GitSource | VcpkgSource) -> dict[str, object]:
    if isinstance(source, ArchiveSource):
        return {
            "kind": "archive",
            "url": source.url,
            "sha256": source.sha256,
            "strip_prefix": source.strip_prefix,
        }
    if isinstance(source, GitSource):
        payload: dict[str, object] = {
            "kind": "git",
            "url": source.url,
            "revision": source.revision,
            "submodules": source.submodules,
        }
        if source.track is not None:
            payload["track"] = source.track
        return payload
    return {
        "kind": "vcpkg",
        "port": source.port,
        "baseline": source.baseline,
        "registry": source.registry,
        "features": list(source.features),
    }


def _source_cache_key(source: ArchiveSource | GitSource | VcpkgSource) -> str:
    encoded = json.dumps(_source_payload(source), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_index_read(store_root: Path, source: ArchiveSource | GitSource | VcpkgSource) -> str | None:
    path = store_root / "requests" / f"{_source_cache_key(source)}.json"
    if not path.is_file():
        return None
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    content_sha256 = payload.get("content_sha256")
    if not isinstance(content_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None:
        return None
    return content_sha256


def _source_index_write(store_root: Path, source: ArchiveSource | GitSource | VcpkgSource, content_sha256: str) -> None:
    directory = store_root / "requests"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_source_cache_key(source)}.json"
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"content_sha256": content_sha256}, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _request_sha256(package: PackageSpec, root: Path) -> str:
    overlay_sha256 = None
    if package.overlay is not None:
        overlay_sha256 = hashlib.sha256((root / package.overlay).read_bytes()).hexdigest()
    source_payload = _source_payload(package.source)
    if isinstance(package.source, GitSource) and package.source.track is not None:
        source_payload = dict(source_payload)
        source_payload.pop("revision")
    payload = {
        "name": package.name,
        "source": source_payload,
        "overlay": package.overlay.as_posix() if package.overlay is not None else None,
        "overlay_sha256": overlay_sha256,
        "options": package.options,
        "features": package.features,
        "components": package.components,
        "linkage": package.linkage,
        "patches": [
            {
                "path": path.as_posix(),
                "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
            }
            for path in package.patches
        ],
        "adapter": package.adapter,
        "build": (
            {
                "kind": "msbuild",
                "project_file": package.build.project_file.as_posix(),
                "target_kind": package.build.kind,
                "defines": package.build.defines,
            }
            if isinstance(package.build, MsbuildProject)
            else None
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        if path.is_symlink():
            raise ConfigurationError(f"Package source contains a symbolic link: {path.relative_to(root).as_posix()}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _archive_relative(name: str) -> Path:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts):
        raise ConfigurationError(f"Package archive member escapes its root: {name}")
    return Path(*path.parts)


def _extract_archive(archive: Path, destination: Path) -> None:
    file_count = 0
    extracted_bytes = 0
    seen: dict[str, str] = {}

    def member_validate(name: str) -> Path:
        relative = _archive_relative(name)
        key = relative.as_posix().casefold()
        previous = seen.get(key)
        if previous is not None:
            raise ConfigurationError(f"Package archive contains colliding members: {previous} and {name}")
        seen[key] = name
        return relative

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                relative = member_validate(member.filename)
                mode = member.external_attr >> 16
                if stat.S_IFMT(mode) == stat.S_IFLNK:
                    raise ConfigurationError(f"Package archive contains a symbolic link: {member.filename}")
                if member.is_dir():
                    (destination / relative).mkdir(parents=True, exist_ok=True)
                    continue
                file_count += 1
                extracted_bytes += member.file_size
                if file_count > _MAX_ARCHIVE_FILES or extracted_bytes > _MAX_EXTRACTED_BYTES:
                    raise ConfigurationError("Package archive exceeds Drift extraction limits")
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, output.open("wb") as target:
                    shutil.copyfileobj(source, target)
                os.utime(output, (_EXTRACTED_MTIME, _EXTRACTED_MTIME))
        return
    try:
        tar_bundle = tarfile.open(archive, mode="r:*")
    except tarfile.TarError as error:
        raise ConfigurationError(f"Unsupported package archive format: {archive}") from error
    with tar_bundle:
        for tar_member in tar_bundle.getmembers():
            tar_relative = member_validate(tar_member.name)
            if tar_member.isdir():
                (destination / tar_relative).mkdir(parents=True, exist_ok=True)
                continue
            if not tar_member.isfile():
                raise ConfigurationError(f"Package archive contains a non-file member: {tar_member.name}")
            file_count += 1
            extracted_bytes += tar_member.size
            if file_count > _MAX_ARCHIVE_FILES or extracted_bytes > _MAX_EXTRACTED_BYTES:
                raise ConfigurationError("Package archive exceeds Drift extraction limits")
            tar_source = tar_bundle.extractfile(tar_member)
            if tar_source is None:
                raise ConfigurationError(f"Cannot extract package archive member: {tar_member.name}")
            tar_output = destination / tar_relative
            tar_output.parent.mkdir(parents=True, exist_ok=True)
            with tar_source, tar_output.open("wb") as target:
                shutil.copyfileobj(tar_source, target)
            os.utime(tar_output, (_EXTRACTED_MTIME, _EXTRACTED_MTIME))


def _download(source: ArchiveSource, destination: Path, offline: bool, declaration_root: Path) -> None:
    parsed = urllib.parse.urlparse(source.url)
    local_path = Path(source.url).expanduser()
    is_local_path = local_path.is_absolute() or parsed.scheme == ""
    if not is_local_path and parsed.scheme not in ("file", "https"):
        raise ConfigurationError(f"Package archives require an HTTPS, file, or local path URL: {source.url}")
    if offline and parsed.scheme == "https":
        raise ConfigurationError(f"Package is not cached and offline mode forbids downloading: {source.url}")
    digest = hashlib.sha256()
    size = 0
    try:
        if is_local_path:
            archive_path = local_path if local_path.is_absolute() else declaration_root / local_path
            input_stream: Any = archive_path.resolve().open("rb")
        else:
            request = urllib.request.Request(source.url, headers={"User-Agent": "drift-build/0"})
            input_stream = urllib.request.urlopen(request, timeout=60)
        with input_stream as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_ARCHIVE_BYTES:
                    raise ConfigurationError("Package archive exceeds Drift's 1 GiB download limit")
                digest.update(chunk)
                output.write(chunk)
    except (OSError, ValueError) as error:
        raise ConfigurationError(f"Cannot download package archive {source.url}: {error}") from error
    actual = digest.hexdigest()
    if actual != source.sha256:
        raise ConfigurationError(f"Package archive checksum mismatch: expected {source.sha256}, got {actual}")


def _git_checkout(source: GitSource, destination: Path, offline: bool, declaration_root: Path) -> None:
    parsed = urllib.parse.urlparse(source.url)
    local_path = Path(source.url).expanduser()
    local = (local_path.is_absolute() or parsed.scheme in ("", "file")) and not source.url.startswith("git@")
    if offline and not local:
        raise ConfigurationError(f"Package is not cached and offline mode forbids cloning: {source.url}")
    url = source.url
    if local_path.is_absolute() or parsed.scheme == "" and not source.url.startswith("git@"):
        url = str((local_path if local_path.is_absolute() else declaration_root / local_path).resolve())
    destination.mkdir(parents=True)
    run(("git", "init", "--quiet"), cwd=destination, capture=True)
    run(("git", "config", "core.autocrlf", "false"), cwd=destination, capture=True)
    run(("git", "config", "core.eol", "lf"), cwd=destination, capture=True)
    run(("git", "remote", "add", "origin", url), cwd=destination, capture=True)
    run(("git", "fetch", "--quiet", "--depth", "1", "origin", source.revision), cwd=destination, capture=True)
    run(("git", "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", "FETCH_HEAD"), cwd=destination)
    result = run(("git", "rev-parse", "HEAD"), cwd=destination, capture=True)
    if result.stdout.strip().casefold() != source.revision.casefold():
        raise ConfigurationError(f"Git package resolved to {result.stdout.strip()}, expected {source.revision}")
    has_submodules = (destination / ".gitmodules").is_file()
    if has_submodules and source.submodules:
        if offline and not local:
            raise ConfigurationError("Offline mode cannot fetch uncached Git submodules")
        run(
            ("git", "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive", "--depth", "1"),
            cwd=destination,
            capture=True,
        )
    elif has_submodules:
        raise ConfigurationError("Git package contains submodules; declare api.git(..., submodules=True)")

    def remove_readonly(function: Any, path: str, _error: Any) -> None:
        os.chmod(path, stat.S_IWRITE)
        function(path)

    metadata = sorted(destination.rglob(".git"), key=lambda path: len(path.parts), reverse=True)
    for path in metadata:
        if path.is_dir():
            shutil.rmtree(path, onexc=remove_readonly)
        else:
            path.unlink()


def _git_revision_resolve(source: GitSource, declaration_root: Path) -> GitSource:
    if source.track is None:
        return source
    parsed = urllib.parse.urlparse(source.url)
    local_path = Path(source.url).expanduser()
    url = source.url
    if local_path.is_absolute() or parsed.scheme == "" and not source.url.startswith("git@"):
        url = str((local_path if local_path.is_absolute() else declaration_root / local_path).resolve())
    result = run(("git", "ls-remote", url, source.track), capture=True)
    matches = [line.split() for line in result.stdout.splitlines() if line.split()]
    peeled = {parts[0].casefold() for parts in matches if len(parts) >= 2 and parts[1].endswith("^{}")}
    revisions = peeled or {parts[0].casefold() for parts in matches if len(parts) >= 2}
    if len(revisions) != 1:
        raise ConfigurationError(f"Git tracking ref {source.track!r} did not resolve unambiguously for {source.url}")
    revision = next(iter(revisions))
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
        raise ConfigurationError(f"Git tracking ref {source.track!r} resolved to an invalid commit")
    return replace(source, revision=revision)


def _patch_apply(source_root: Path, patch: Path) -> None:
    try:
        text = patch.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"Cannot read package patch {patch}: {error}") from error
    for line in text.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        value = line[4:].split("\t", 1)[0].strip()
        if value == "/dev/null":
            continue
        parts = PurePosixPath(value.replace("\\", "/")).parts
        if not parts or ".." in parts or PurePosixPath(value).is_absolute():
            raise ConfigurationError(f"Package patch path escapes its source root: {value}")
    command = (
        "git",
        "-c",
        "core.autocrlf=false",
        "apply",
        "--ignore-whitespace",
        "--whitespace=nowarn",
    )
    run((*command, "--check", str(patch)), cwd=source_root, capture=True)
    run((*command, str(patch)), cwd=source_root, capture=True)


def _source_prepare(package: PackageSpec, temporary: Path, offline: bool, declaration_root: Path) -> Path:
    source_root = temporary / "source"
    if isinstance(package.source, ArchiveSource):
        archive = temporary / "archive"
        _download(package.source, archive, offline, declaration_root)
        unpacked = temporary / "unpacked"
        unpacked.mkdir()
        try:
            _extract_archive(archive, unpacked)
        except ConfigurationError:
            raise
        except (OSError, EOFError, RuntimeError, tarfile.TarError, zipfile.BadZipFile) as error:
            raise ConfigurationError(f"Cannot extract package archive {package.source.url}: {error}") from error
        if package.source.strip_prefix is None:
            source_root = unpacked
        else:
            prefix = _archive_relative(package.source.strip_prefix)
            source_root = unpacked / prefix
            if not source_root.is_dir():
                raise ConfigurationError(
                    f"Package archive does not contain strip_prefix {package.source.strip_prefix!r}"
                )
    elif isinstance(package.source, GitSource):
        _git_checkout(package.source, source_root, offline, declaration_root)
    else:
        source_root.mkdir(parents=True)
        manifest = {
            "port": package.source.port,
            "baseline": package.source.baseline,
            "registry": package.source.registry,
            "features": package.source.features,
        }
        (source_root / "drift-vcpkg.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    for patch in package.patches:
        _patch_apply(source_root, (declaration_root / patch).resolve())
    return source_root


def _source_materialize_unlocked(
    package: PackageSpec,
    store_root: Path,
    declaration_root: Path,
    *,
    expected_content: str | None,
    offline: bool,
    verify_cached: bool,
    refresh: bool,
) -> tuple[Path, str]:
    sources = store_root / "sources"
    cached_content = None if refresh else expected_content or _source_index_read(store_root, package.source)
    if cached_content is not None:
        existing = sources / cached_content
        if existing.is_dir():
            if verify_cached and _tree_sha256(existing) != cached_content:
                raise ConfigurationError(f"Cached package content is corrupt: {existing}")
            return existing, cached_content
    sources.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"drift-{package.name}-", dir=sources) as temporary_text:
        source_root = _source_prepare(package, Path(temporary_text), offline, declaration_root)
        content_sha256 = _tree_sha256(source_root)
        if expected_content is not None and content_sha256 != expected_content:
            raise ConfigurationError(
                f"Package {package.name} content mismatch: expected {expected_content}, got {content_sha256}"
            )
        install = sources / content_sha256
        if refresh and install.is_dir() and _tree_sha256(install) != content_sha256:
            shutil.rmtree(install)
        if not install.exists():
            staged = Path(temporary_text) / "install"
            os.replace(source_root, staged)
            try:
                os.replace(staged, install)
            except OSError:
                if not install.is_dir():
                    raise
        _source_index_write(store_root, package.source, content_sha256)
        return install, content_sha256


def _source_materialize(
    package: PackageSpec,
    store_root: Path,
    declaration_root: Path,
    *,
    expected_content: str | None,
    offline: bool,
    verify_cached: bool,
    refresh: bool = False,
) -> tuple[Path, str]:
    lock = store_root / "locks" / f"{_source_cache_key(package.source)}.lock"
    with cache_lock(lock):
        return _source_materialize_unlocked(
            package,
            store_root,
            declaration_root,
            expected_content=expected_content,
            offline=offline,
            verify_cached=verify_cached,
            refresh=refresh,
        )


def _locked_to_json(package: LockedPackage) -> dict[str, object]:
    return {
        "name": package.name,
        "request_sha256": package.request_sha256,
        "content_sha256": package.content_sha256,
        "source": _source_payload(package.source),
        "overlay": package.overlay,
        "provenance": dict(package.provenance),
        "scope": package.scope,
    }


def _source_from_json(payload: object) -> ArchiveSource | GitSource | VcpkgSource:
    if not isinstance(payload, dict):
        raise ConfigurationError("Package lock source must be an object")
    kind = payload.get("kind")
    if kind == "vcpkg":
        port = payload.get("port")
        baseline = payload.get("baseline")
        registry = payload.get("registry")
        features = payload.get("features", [])
        if (
            not isinstance(port, str)
            or not isinstance(baseline, str)
            or not isinstance(registry, str)
            or not isinstance(features, list)
            or not all(isinstance(value, str) for value in features)
        ):
            raise ConfigurationError("Invalid vcpkg source in package lock")
        return VcpkgSource(port, baseline, registry, tuple(features))
    url = payload.get("url")
    if not isinstance(url, str):
        raise ConfigurationError("Package lock source requires a URL")
    if kind == "archive":
        sha256 = payload.get("sha256")
        strip_prefix = payload.get("strip_prefix")
        if not isinstance(sha256, str) or strip_prefix is not None and not isinstance(strip_prefix, str):
            raise ConfigurationError("Invalid archive source in package lock")
        if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ConfigurationError("Invalid archive SHA-256 in package lock")
        return ArchiveSource(url, sha256, strip_prefix)
    if kind == "git":
        revision = payload.get("revision")
        submodules = payload.get("submodules", False)
        if not isinstance(revision, str) or not isinstance(submodules, bool):
            raise ConfigurationError("Invalid Git source in package lock")
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
            raise ConfigurationError("Invalid Git revision in package lock")
        track = payload.get("track")
        if track is not None and not isinstance(track, str):
            raise ConfigurationError("Invalid Git tracking ref in package lock")
        return GitSource(url, revision, submodules, track)
    raise ConfigurationError(f"Unknown package source kind in lock: {kind!r}")


def package_lock_read(root: Path) -> PackageLock:
    """Read and validate the project's drift.lock package state."""
    path = root / "drift.lock"
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Cannot read {path}; run 'drift lock': {error}") from error
    if not isinstance(payload, dict) or payload.get("version") not in (1, 2, 3, LOCK_VERSION):
        raise ConfigurationError(f"Unsupported or invalid package lock: {path}")
    values = payload.get("packages")
    if not isinstance(values, list):
        raise ConfigurationError("Package lock requires a packages array")
    packages: list[LockedPackage] = []
    for value in values:
        if not isinstance(value, dict):
            raise ConfigurationError("Package lock entry must be an object")
        name = value.get("name")
        request_sha256 = value.get("request_sha256")
        content_sha256 = value.get("content_sha256")
        overlay = value.get("overlay")
        provenance = value.get("provenance", {})
        scope = value.get("scope", "")
        if not all(isinstance(item, str) for item in (name, request_sha256, content_sha256)):
            raise ConfigurationError("Package lock entry is missing an identity or digest")
        assert isinstance(name, str)
        assert isinstance(request_sha256, str)
        assert isinstance(content_sha256, str)
        if (
            re.fullmatch(r"[0-9a-f]{64}", request_sha256) is None
            or re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None
        ):
            raise ConfigurationError("Package lock contains an invalid digest")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None or name in (".", ".."):
            raise ConfigurationError("Package lock contains an invalid package name")
        if overlay is not None and not isinstance(overlay, str):
            raise ConfigurationError("Package lock overlay must be a path or null")
        if not isinstance(provenance, dict) or not all(isinstance(key, str) for key in provenance):
            raise ConfigurationError("Package lock provenance must be an object")
        if not isinstance(scope, str) or scope and any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) is None for part in scope.split("/")
        ):
            raise ConfigurationError("Package lock scope must contain package-name path segments")
        packages.append(
            LockedPackage(
                name,
                request_sha256,
                content_sha256,
                _source_from_json(value.get("source")),
                overlay,
                provenance,
                scope,
            )
        )
    if len({(package.scope, package.name) for package in packages}) != len(packages):
        raise ConfigurationError("Package lock contains duplicate scoped package names")
    return PackageLock(tuple(packages))


def package_lock_create(
    project: ProjectSpec,
    root: Path,
    store_root: Path | None = None,
    *,
    refresh: bool = False,
    write: bool = True,
) -> PackageLock:
    """Resolve exact package declarations, materialize them, and replace drift.lock."""
    store = store_root or package_store_root()
    previous: dict[tuple[str, str], LockedPackage] = {}
    if (root / "drift.lock").is_file():
        previous = {(package.scope, package.name): package for package in package_lock_read(root).packages}
    locked: list[LockedPackage] = []
    visiting: set[str] = set()

    def resolve(packages: tuple[PackageSpec, ...], declaration_root: Path, scope: str) -> None:
        for package in sorted(packages, key=lambda item: item.name):
            request_sha256 = _request_sha256(package, declaration_root)
            existing = previous.get((scope, package.name))
            locked_source = package.source
            if isinstance(package.source, GitSource) and package.source.track is not None:
                if (
                    not refresh
                    and existing is not None
                    and existing.request_sha256 == request_sha256
                    and isinstance(existing.source, GitSource)
                ):
                    locked_source = existing.source
                else:
                    locked_source = _git_revision_resolve(package.source, declaration_root)
            resolved_package = replace(package, source=locked_source)
            expected = (
                existing.content_sha256
                if not refresh and existing is not None and existing.request_sha256 == request_sha256
                else None
            )
            path, content_sha256 = _source_materialize(
                resolved_package,
                store,
                declaration_root,
                expected_content=expected,
                offline=False,
                verify_cached=True,
                refresh=refresh,
            )
            from driftbuild.importers import package_provenance

            locked.append(
                LockedPackage(
                    package.name,
                    request_sha256,
                    content_sha256,
                    locked_source,
                    package.overlay.as_posix() if package.overlay is not None else None,
                    package_provenance(path, resolved_package, sys.platform),
                    scope,
                )
            )
            identity = _source_cache_key(locked_source)
            if identity in visiting:
                chain = "/".join(part for part in (scope, package.name) if part)
                raise ConfigurationError(f"Transitive package cycle detected at {chain}")
            nested: ProjectSpec | None = None
            if package.overlay is not None:
                try:
                    nested = _overlay_load(
                        declaration_root / package.overlay, path, BuildConfig(sys.platform), package.name
                    )
                except ConfigurationError:
                    # Existing overlays may describe only a subset of package
                    # files. Composition reports their concrete provider error.
                    nested = None
            elif package.build is None and (path / "drift.toml").is_file():
                nested = project_load(path, BuildConfig(sys.platform))
            if nested is not None and nested.packages:
                visiting.add(identity)
                child_scope = "/".join(part for part in (scope, package.name) if part)
                resolve(nested.packages, path, child_scope)
                visiting.remove(identity)

    resolve(project.packages, root, "")
    result = PackageLock(tuple(locked))
    payload = {"version": LOCK_VERSION, "packages": [_locked_to_json(package) for package in result.packages]}
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if write:
        path = root / "drift.lock"
        temporary = path.with_suffix(".lock.tmp")
        temporary.write_bytes(encoded)
        os.replace(temporary, path)
    return result


def package_lock_diff(before: PackageLock | None, after: PackageLock) -> tuple[str, ...]:
    """Return a stable human-readable package lock change summary."""
    previous = {(package.scope, package.name): package for package in before.packages} if before is not None else {}
    current = {(package.scope, package.name): package for package in after.packages}
    changes: list[str] = []
    for key in sorted(previous.keys() - current.keys()):
        changes.append(f"- {'/'.join(part for part in key if part)}")
    for key in sorted(current.keys() - previous.keys()):
        changes.append(f"+ {'/'.join(part for part in key if part)} {current[key].content_sha256[:12]}")
    for key in sorted(previous.keys() & current.keys()):
        old = previous[key]
        new = current[key]
        if _locked_to_json(old) != _locked_to_json(new):
            name = "/".join(part for part in key if part)
            changes.append(f"~ {name} {old.content_sha256[:12]} -> {new.content_sha256[:12]}")
    return tuple(changes)


def _locked_validate(project: ProjectSpec, root: Path, lock: PackageLock) -> dict[str, LockedPackage]:
    requested = {package.name: package for package in project.packages}
    locked = {package.name: package for package in lock.packages if not package.scope}
    if requested.keys() != locked.keys():
        raise ConfigurationError("drift.lock package set is stale; run 'drift lock'")
    for name, package in requested.items():
        if _request_sha256(package, root) != locked[name].request_sha256:
            raise ConfigurationError(f"drift.lock entry for {name} is stale; run 'drift lock'")
    return locked


def packages_fetch(
    project: ProjectSpec,
    root: Path,
    *,
    store_root: Path | None = None,
    offline: bool = False,
    verify_cached: bool = True,
) -> dict[str, Path]:
    """Materialize every locked package and return its immutable source root."""
    if not project.packages:
        return {}
    store = store_root or package_store_root()
    locked = _locked_validate(project, root, package_lock_read(root))
    roots: dict[str, Path] = {}
    for package in sorted(project.packages, key=lambda item: item.name):
        resolved_package = replace(package, source=locked[package.name].source)
        roots[package.name], _digest = _source_materialize(
            resolved_package,
            store,
            root,
            expected_content=locked[package.name].content_sha256,
            offline=offline,
            verify_cached=verify_cached,
        )
    return roots


def _overlay_load(path: Path, package_root: Path, config: BuildConfig, package_name: str) -> ProjectSpec:
    module_name = f"_drift_overlay_{package_name}_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ConfigurationError(f"Cannot load package overlay: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(path.parent))
    sys.path.insert(0, str(package_root))
    try:
        spec.loader.exec_module(module)
        function = module.project
        result = function(ProjectApi(package_root, config))
    except (ImportError, AttributeError, OSError, TypeError) as error:
        raise ConfigurationError(f"Cannot load package overlay {path}: {error}") from error
    finally:
        sys.path.pop(0)
        sys.path.pop(0)
    if not isinstance(result, ProjectSpec):
        raise ConfigurationError(f"Package overlay {path} did not return ProjectSpec")
    return result


def _package_target_name(package: str, target: str) -> str:
    package_readable = re.sub(r"[^A-Za-z0-9_.-]", "_", package)
    readable = re.sub(r"[^A-Za-z0-9_.-]", "_", target)
    digest = hashlib.sha256(f"{package}\0{target}".encode("utf-8")).hexdigest()[:12]
    return f"__drift_package_{package_readable}_{readable}_{digest}"


def _package_path(
    package_root: Path,
    value: Path,
    extra_roots: tuple[Path, ...] = (),
    *,
    allow_external: bool = False,
) -> Path:
    resolved = value.resolve() if value.is_absolute() else (package_root / value).resolve()
    for allowed in (package_root, *extra_roots):
        try:
            resolved.relative_to(allowed.resolve())
            return resolved
        except ValueError:
            continue
    if allow_external and value.is_absolute():
        return resolved
    raise ConfigurationError(f"Package path escapes its source and import roots: {value}")


def _package_output(package: str, value: Path, extra_roots: tuple[Path, ...]) -> Path:
    if value.is_absolute():
        return _package_path(extra_roots[0], value, extra_roots[1:])
    if ".." in value.parts:
        raise ConfigurationError(f"Package output escapes its build root: {value}")
    return Path("packages") / package / value


def _input_rebase(
    value: BuildInput,
    package_root: Path,
    names: dict[str, str],
    extra_roots: tuple[Path, ...],
    allow_external: bool,
) -> BuildInput:
    if isinstance(value, Path):
        return _package_path(package_root, value, extra_roots, allow_external=allow_external)
    return Artifact(TargetRef(names[value.target.name]), value.path)


def _dependency_rebase(
    dependency: Dependency,
    package_root: Path,
    names: dict[str, str],
    extra_roots: tuple[Path, ...],
    allow_external: bool,
) -> Dependency:
    libraries = tuple(
        _package_path(package_root, value, extra_roots, allow_external=allow_external)
        if isinstance(value, Path)
        else value
        for value in dependency.link.libraries
    )
    return Dependency(
        dependency.name,
        CompileInterface(
            tuple(
                _package_path(package_root, value, extra_roots, allow_external=allow_external)
                for value in dependency.compile.include_dirs
            ),
            dependency.compile.defines,
            dependency.compile.arguments,
        ),
        LinkInterface(
            libraries,
            tuple(
                _package_path(package_root, value, extra_roots, allow_external=allow_external)
                for value in dependency.link.library_dirs
            ),
            dependency.link.arguments,
        ),
        tuple(
            Deployment(
                _input_rebase(value.source, package_root, names, extra_roots, allow_external),
                value.destination,
            )
            if isinstance(value, Deployment)
            else _input_rebase(value, package_root, names, extra_roots, allow_external)
            for value in dependency.runtime_files
        ),
    )


def _package_project_transform(
    package: PackageSpec,
    package_namespace: str,
    package_root: Path,
    project: ProjectSpec,
    transitive_exports: Mapping[tuple[str, str], str],
    extra_roots: tuple[Path, ...] = (),
    *,
    allow_external: bool = False,
) -> tuple[TargetSpec, ...]:
    if (
        project.pools
        or project.commands
        or project.tasks
        or project.tests
        or project.benchmarks
        or project.artifacts
        or project.releases
        or project.remotes
        or project.github is not None
    ):
        raise ConfigurationError(f"Package {package.name} must expose only build targets")
    names = {target.name: _package_target_name(package_namespace, target.name) for target in project.targets}
    transformed: list[TargetSpec] = []
    for target in project.targets:
        dependencies: list[Dependency | TargetDependency] = []
        for dependency in target.dependencies:
            if isinstance(dependency, Dependency):
                dependencies.append(_dependency_rebase(dependency, package_root, names, extra_roots, allow_external))
            elif isinstance(dependency.target, PackageTargetRef):
                key = dependency.target.package, dependency.target.target
                selected = transitive_exports.get(key)
                if selected is None:
                    if not key[1]:
                        raise ConfigurationError(
                            f"Transitive package {key[0]} does not have one unambiguous default target"
                        )
                    raise ConfigurationError(f"Transitive package target does not exist: @{key[0]}//{key[1]}")
                dependencies.append(TargetDependency(TargetRef(selected), dependency.visibility))
            else:
                dependencies.append(TargetDependency(TargetRef(names[dependency.target.name]), dependency.visibility))
        outputs = tuple(_package_output(package_namespace, output, extra_roots) for output in target.outputs)
        action = target.action
        if action is not None:
            action = replace(
                action,
                outputs=outputs,
                inputs=tuple(
                    _input_rebase(value, package_root, names, extra_roots, allow_external) for value in action.inputs
                ),
                implicit_inputs=tuple(
                    _input_rebase(value, package_root, names, extra_roots, allow_external)
                    for value in action.implicit_inputs
                ),
                order_only=tuple(
                    _input_rebase(value, package_root, names, extra_roots, allow_external)
                    for value in action.order_only
                ),
                depfile=(
                    _package_output(package_namespace, action.depfile, extra_roots)
                    if action.depfile is not None
                    else None
                ),
            )
        transformed.append(
            replace(
                target,
                name=names[target.name],
                sources=tuple(
                    _input_rebase(value, package_root, names, extra_roots, allow_external) for value in target.sources
                ),
                public_headers=tuple(
                    _input_rebase(value, package_root, names, extra_roots, allow_external)
                    for value in target.public_headers
                ),
                private_headers=tuple(
                    _input_rebase(value, package_root, names, extra_roots, allow_external)
                    for value in target.private_headers
                ),
                include_dirs=tuple(
                    _package_path(package_root, value, extra_roots, allow_external=allow_external)
                    for value in target.include_dirs
                ),
                dependencies=tuple(dependencies),
                objects=tuple(TargetRef(names[reference.name]) for reference in target.objects),
                runtime_files=tuple(
                    Deployment(
                        _input_rebase(value.source, package_root, names, extra_roots, allow_external),
                        value.destination,
                    )
                    if isinstance(value, Deployment)
                    else _input_rebase(value, package_root, names, extra_roots, allow_external)
                    for value in target.runtime_files
                ),
                outputs=outputs,
                action=action,
                precompiled_header=(
                    _package_path(
                        package_root,
                        target.precompiled_header,
                        extra_roots,
                        allow_external=allow_external,
                    )
                    if target.precompiled_header is not None
                    else None
                ),
            )
        )
    return tuple(transformed)


def packages_compose(
    project: ProjectSpec,
    root: Path,
    config: BuildConfig,
    *,
    store_root: Path | None = None,
    offline: bool = False,
) -> ProjectSpec:
    """Load locked package projects and compose their namespaced targets into the root graph."""
    if not project.packages:
        return project
    store = store_root or package_store_root()
    lock = package_lock_read(root)
    _locked_validate(project, root, lock)
    locked = {(package.scope, package.name): package for package in lock.packages}
    package_targets: list[TargetSpec] = []
    exported: dict[tuple[str, str], str] = {}

    def compose_one(
        package: PackageSpec, declaration_root: Path, scope: str
    ) -> tuple[tuple[TargetSpec, ...], dict[str, str]]:
        lock_entry = locked.get((scope, package.name))
        if lock_entry is None or _request_sha256(package, declaration_root) != lock_entry.request_sha256:
            qualified = "/".join(part for part in (scope, package.name) if part)
            raise ConfigurationError(f"drift.lock entry for {qualified} is stale; run 'drift lock'")
        resolved_package = replace(package, source=lock_entry.source)
        package_root, _digest = _source_materialize(
            resolved_package,
            store,
            declaration_root,
            expected_content=lock_entry.content_sha256,
            offline=offline,
            verify_cached=False,
        )
        namespace = "/".join(part for part in (scope, package.name) if part)
        import_root = root / ".drift" / "imports"
        allow_external = False
        if package.overlay is not None:
            dependency_project = _overlay_load(
                declaration_root / package.overlay, package_root, config, package.name
            )
        elif package.build is None and (package_root / "drift.toml").is_file():
            dependency_project = project_load(package_root, config)
        else:
            from driftbuild.importers import project_import

            dependency_project = project_import(package_root, import_root, config, package, offline=offline)
            allow_external = True
        from driftbuild.package_cache import binary_cache_root

        nested_targets: list[TargetSpec] = []
        nested_exports: dict[tuple[str, str], str] = {}
        for child in sorted(dependency_project.packages, key=lambda item: item.name):
            child_targets, child_export = compose_one(child, package_root, namespace)
            nested_targets.extend(child_targets)
            nested_exports.update({(child.name, name): value for name, value in child_export.items()})
        targets = _package_project_transform(
            package,
            namespace,
            package_root,
            dependency_project,
            nested_exports,
            (import_root, binary_cache_root()),
            allow_external=allow_external,
        )
        current_exports = {
            original.name: transformed.name
            for original, transformed in zip(dependency_project.targets, targets, strict=True)
        }
        default_names = [reference.name for reference in dependency_project.defaults]
        if len(default_names) == 1:
            current_exports[""] = _package_target_name(namespace, default_names[0])
        elif not default_names and len(dependency_project.targets) == 1:
            current_exports[""] = targets[0].name
        return tuple((*nested_targets, *targets)), current_exports

    for package in sorted(project.packages, key=lambda item: item.name):
        targets, package_exports = compose_one(package, root, "")
        package_targets.extend(targets)
        exported.update({(package.name, name): value for name, value in package_exports.items()})

    local_targets: list[TargetSpec] = []
    for target in project.targets:
        dependencies: list[Dependency | TargetDependency] = []
        for dependency in target.dependencies:
            if not isinstance(dependency, TargetDependency) or not isinstance(dependency.target, PackageTargetRef):
                dependencies.append(dependency)
                continue
            key = dependency.target.package, dependency.target.target
            selected = exported.get(key)
            if selected is None:
                if key[1] == "":
                    raise ConfigurationError(f"Package {key[0]} does not have one unambiguous default target")
                raise ConfigurationError(f"Package target does not exist: @{key[0]}//{key[1]}")
            dependencies.append(TargetDependency(TargetRef(selected), dependency.visibility))
        local_targets.append(replace(target, dependencies=tuple(dependencies)))
    return replace(project, targets=tuple((*package_targets, *local_targets)), packages=())
