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
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from driftbuild.errors import ConfigurationError
from driftbuild.model import (
    ArchiveSource,
    Artifact,
    BuildConfig,
    BuildInput,
    CompileInterface,
    Dependency,
    GitSource,
    LinkInterface,
    PackageSpec,
    PackageTargetRef,
    ProjectSpec,
    TargetDependency,
    TargetRef,
    TargetSpec,
)
from driftbuild.process import run
from driftbuild.project import ProjectApi, project_load

LOCK_VERSION = 1
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
_MAX_ARCHIVE_FILES = 100_000


@dataclass(frozen=True)
class LockedPackage:
    """One exact package source recorded in drift.lock."""

    name: str
    request_sha256: str
    content_sha256: str
    source: ArchiveSource | GitSource
    overlay: str | None


@dataclass(frozen=True)
class PackageLock:
    """Complete deterministic package lock state."""

    packages: tuple[LockedPackage, ...]


def package_store_root() -> Path:
    """Return the shared Drift content store location."""
    override = os.environ.get("DRIFT_HOME")
    if override:
        return Path(override).expanduser().resolve() / "store"
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "drift" / "store"
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / "drift" / "store"


def _source_payload(source: ArchiveSource | GitSource) -> dict[str, str | None]:
    if isinstance(source, ArchiveSource):
        return {
            "kind": "archive",
            "url": source.url,
            "sha256": source.sha256,
            "strip_prefix": source.strip_prefix,
        }
    return {"kind": "git", "url": source.url, "revision": source.revision}


def _source_cache_key(source: ArchiveSource | GitSource) -> str:
    encoded = json.dumps(_source_payload(source), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_index_read(store_root: Path, source: ArchiveSource | GitSource) -> str | None:
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


def _source_index_write(store_root: Path, source: ArchiveSource | GitSource, content_sha256: str) -> None:
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
    payload = {
        "name": package.name,
        "source": _source_payload(package.source),
        "overlay": package.overlay.as_posix() if package.overlay is not None else None,
        "overlay_sha256": overlay_sha256,
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

    def remove_readonly(function: Any, path: str, _error: Any) -> None:
        os.chmod(path, stat.S_IWRITE)
        function(path)

    shutil.rmtree(destination / ".git", onexc=remove_readonly)
    if has_submodules:
        raise ConfigurationError("Git packages with submodules are not supported yet")


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
    else:
        _git_checkout(package.source, source_root, offline, declaration_root)
    return source_root


def _source_materialize(
    package: PackageSpec,
    store_root: Path,
    declaration_root: Path,
    *,
    expected_content: str | None,
    offline: bool,
    verify_cached: bool,
) -> tuple[Path, str]:
    sources = store_root / "sources"
    cached_content = expected_content or _source_index_read(store_root, package.source)
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


def _locked_to_json(package: LockedPackage) -> dict[str, object]:
    return {
        "name": package.name,
        "request_sha256": package.request_sha256,
        "content_sha256": package.content_sha256,
        "source": _source_payload(package.source),
        "overlay": package.overlay,
    }


def _source_from_json(payload: object) -> ArchiveSource | GitSource:
    if not isinstance(payload, dict):
        raise ConfigurationError("Package lock source must be an object")
    kind = payload.get("kind")
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
        if not isinstance(revision, str):
            raise ConfigurationError("Invalid Git source in package lock")
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
            raise ConfigurationError("Invalid Git revision in package lock")
        return GitSource(url, revision)
    raise ConfigurationError(f"Unknown package source kind in lock: {kind!r}")


def package_lock_read(root: Path) -> PackageLock:
    """Read and validate the project's drift.lock package state."""
    path = root / "drift.lock"
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Cannot read {path}; run 'drift lock': {error}") from error
    if not isinstance(payload, dict) or payload.get("version") != LOCK_VERSION:
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
        packages.append(
            LockedPackage(name, request_sha256, content_sha256, _source_from_json(value.get("source")), overlay)
        )
    if len({package.name for package in packages}) != len(packages):
        raise ConfigurationError("Package lock contains duplicate package names")
    return PackageLock(tuple(packages))


def package_lock_create(project: ProjectSpec, root: Path, store_root: Path | None = None) -> PackageLock:
    """Resolve exact package declarations, materialize them, and replace drift.lock."""
    store = store_root or package_store_root()
    previous: dict[str, LockedPackage] = {}
    if (root / "drift.lock").is_file():
        previous = {package.name: package for package in package_lock_read(root).packages}
    locked: list[LockedPackage] = []
    for package in sorted(project.packages, key=lambda item: item.name):
        request_sha256 = _request_sha256(package, root)
        existing = previous.get(package.name)
        expected = (
            existing.content_sha256 if existing is not None and existing.request_sha256 == request_sha256 else None
        )
        _path, content_sha256 = _source_materialize(
            package,
            store,
            root,
            expected_content=expected,
            offline=False,
            verify_cached=True,
        )
        locked.append(
            LockedPackage(
                package.name,
                request_sha256,
                content_sha256,
                package.source,
                package.overlay.as_posix() if package.overlay is not None else None,
            )
        )
    result = PackageLock(tuple(locked))
    payload = {"version": LOCK_VERSION, "packages": [_locked_to_json(package) for package in result.packages]}
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path = root / "drift.lock"
    temporary = path.with_suffix(".lock.tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return result


def _locked_validate(project: ProjectSpec, root: Path, lock: PackageLock) -> dict[str, LockedPackage]:
    requested = {package.name: package for package in project.packages}
    locked = {package.name: package for package in lock.packages}
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
        roots[package.name], _digest = _source_materialize(
            package,
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
    readable = re.sub(r"[^A-Za-z0-9_.-]", "_", target)
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()[:12]
    return f"__drift_package_{package}_{readable}_{digest}"


def _package_path(package_root: Path, value: Path) -> Path:
    resolved = value.resolve() if value.is_absolute() else (package_root / value).resolve()
    try:
        resolved.relative_to(package_root.resolve())
    except ValueError as error:
        raise ConfigurationError(f"Package path escapes its source root: {value}") from error
    return resolved


def _package_output(package: str, value: Path) -> Path:
    if value.is_absolute() or ".." in value.parts:
        raise ConfigurationError(f"Package output escapes its build root: {value}")
    return Path("packages") / package / value


def _input_rebase(value: BuildInput, package_root: Path, names: dict[str, str]) -> BuildInput:
    if isinstance(value, Path):
        return _package_path(package_root, value)
    return Artifact(TargetRef(names[value.target.name]), value.path)


def _dependency_rebase(dependency: Dependency, package_root: Path) -> Dependency:
    libraries = tuple(
        _package_path(package_root, value) if isinstance(value, Path) else value for value in dependency.link.libraries
    )
    return Dependency(
        dependency.name,
        CompileInterface(
            tuple(_package_path(package_root, value) for value in dependency.compile.include_dirs),
            dependency.compile.defines,
            dependency.compile.arguments,
        ),
        LinkInterface(
            libraries,
            tuple(_package_path(package_root, value) for value in dependency.link.library_dirs),
            dependency.link.arguments,
        ),
        tuple(_package_path(package_root, value) for value in dependency.runtime_files),
    )


def _package_project_transform(
    package: PackageSpec, package_root: Path, project: ProjectSpec
) -> tuple[TargetSpec, ...]:
    if project.packages:
        raise ConfigurationError(f"Package {package.name} declares transitive packages; this is not supported yet")
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
    names = {target.name: _package_target_name(package.name, target.name) for target in project.targets}
    transformed: list[TargetSpec] = []
    for target in project.targets:
        if target.action is not None or target.kind in ("custom", "external_library", "runtime_bundle"):
            raise ConfigurationError(f"Package {package.name} target {target.name} uses an unsupported action target")
        dependencies: list[Dependency | TargetDependency] = []
        for dependency in target.dependencies:
            if isinstance(dependency, Dependency):
                dependencies.append(_dependency_rebase(dependency, package_root))
            elif isinstance(dependency.target, PackageTargetRef):
                raise ConfigurationError(f"Package {package.name} target {target.name} uses a transitive package")
            else:
                dependencies.append(TargetDependency(TargetRef(names[dependency.target.name]), dependency.visibility))
        outputs = tuple(_package_output(package.name, output) for output in target.outputs)
        transformed.append(
            replace(
                target,
                name=names[target.name],
                sources=tuple(_input_rebase(value, package_root, names) for value in target.sources),
                public_headers=tuple(_input_rebase(value, package_root, names) for value in target.public_headers),
                private_headers=tuple(_input_rebase(value, package_root, names) for value in target.private_headers),
                include_dirs=tuple(_package_path(package_root, value) for value in target.include_dirs),
                dependencies=tuple(dependencies),
                objects=tuple(TargetRef(names[reference.name]) for reference in target.objects),
                runtime_files=tuple(_input_rebase(value, package_root, names) for value in target.runtime_files),
                outputs=outputs,
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
    package_roots = packages_fetch(
        project,
        root,
        store_root=store_root,
        offline=offline,
        verify_cached=False,
    )
    package_targets: list[TargetSpec] = []
    exported: dict[tuple[str, str], str] = {}
    for package in sorted(project.packages, key=lambda item: item.name):
        package_root = package_roots[package.name]
        if package.overlay is None:
            dependency_project = project_load(package_root, config)
        else:
            dependency_project = _overlay_load(root / package.overlay, package_root, config, package.name)
        targets = _package_project_transform(package, package_root, dependency_project)
        package_targets.extend(targets)
        exported.update(
            ((package.name, original.name), transformed.name)
            for original, transformed in zip(dependency_project.targets, targets, strict=True)
        )

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
                raise ConfigurationError(f"Package target does not exist: @{key[0]}//{key[1]}")
            dependencies.append(TargetDependency(TargetRef(selected), dependency.visibility))
        local_targets.append(replace(target, dependencies=tuple(dependencies)))
    return replace(project, targets=tuple((*package_targets, *local_targets)), packages=())
