"""Provider-facing declarations and project loading."""

from __future__ import annotations

import importlib
import os
import re
import sys
import tomllib
import urllib.parse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from driftbuild.errors import ConfigurationError
from driftbuild.model import (
    ActionSpec,
    ArchiveSource,
    Artifact,
    ArtifactSpec,
    BenchmarkSpec,
    BuildConfig,
    BuildInput,
    CommandSpec,
    CompileInterface,
    Dependency,
    FileSet,
    GitHubSpec,
    GitSource,
    LinkInterface,
    MsbuildProject,
    PackageBuild,
    PackageRef,
    PackageSource,
    PackageSpec,
    PackageTargetRef,
    PoolSpec,
    ProjectSpec,
    ReleaseSpec,
    RemoteSpec,
    TargetDependency,
    TargetKind,
    TargetRef,
    TargetSpec,
    TaskSpec,
    TestSpec,
)


def _safe_relative(root: Path, value: str | os.PathLike[str], *, must_exist: bool) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ConfigurationError(f"Path escapes the project root: {value}") from error
    if must_exist and not resolved.exists():
        raise ConfigurationError(f"Project input does not exist: {relative.as_posix()}")
    return relative


def _inputs(values: FileSet | BuildInput | Sequence[BuildInput] | None) -> tuple[BuildInput, ...]:
    if values is None:
        return ()
    if isinstance(values, FileSet):
        return tuple(values.files)
    if isinstance(values, (Path, Artifact)):
        return (values,)
    return tuple(values)


class ProjectApi:
    """Builds one immutable project declaration for a selected configuration."""

    def __init__(self, root: Path, config: BuildConfig):
        self.root = root.resolve()
        self.config = config
        self._targets: dict[str, TargetSpec] = {}
        self._packages: dict[str, PackageSpec] = {}
        self._commands: list[CommandSpec] = []
        self._tasks: list[TaskSpec] = []
        self._pools: list[PoolSpec] = []
        self._tests: list[TestSpec] = []
        self._benchmarks: list[BenchmarkSpec] = []
        self._artifacts: list[ArtifactSpec] = []
        self._releases: list[ReleaseSpec] = []
        self._remotes: list[RemoteSpec] = []
        self._github: GitHubSpec | None = None

    def files(self, *paths: str | os.PathLike[str]) -> FileSet:
        """Return explicit, validated repository-relative files."""
        return FileSet(tuple(_safe_relative(self.root, path, must_exist=True) for path in paths))

    def tree(
        self,
        root: str | os.PathLike[str],
        *,
        include: Sequence[str] = ("**/*",),
        exclude: Sequence[str] = (),
    ) -> FileSet:
        """Discover a deterministic, root-confined set of files."""
        relative_root = _safe_relative(self.root, root, must_exist=True)
        absolute_root = self.root / relative_root
        if not absolute_root.is_dir():
            raise ConfigurationError(f"Tree root is not a directory: {relative_root.as_posix()}")

        found: dict[str, Path] = {}
        excluded: set[Path] = set()
        for pattern in exclude:
            excluded.update(path.resolve() for path in absolute_root.glob(pattern))
        for pattern in include:
            for path in absolute_root.glob(pattern):
                if path.is_symlink() or not path.is_file() or path.resolve() in excluded:
                    continue
                relative = path.resolve().relative_to(self.root)
                key = relative.as_posix().casefold()
                previous = found.get(key)
                if previous is not None and previous != relative:
                    raise ConfigurationError(
                        f"Case-colliding project inputs: {previous.as_posix()} and {relative.as_posix()}"
                    )
                found[key] = relative
        return FileSet(tuple(sorted(found.values(), key=lambda path: path.as_posix())))

    def command_action(
        self,
        command: Sequence[str],
        *,
        outputs: Sequence[str | os.PathLike[str]],
        inputs: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        implicit_inputs: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        order_only: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        environment: Mapping[str, str] | None = None,
        depfile: str | os.PathLike[str] | None = None,
        deps: str | None = None,
        description: str | None = None,
        pool: str | None = None,
        timeout_seconds: float | None = None,
        restat: bool = False,
    ) -> ActionSpec:
        """Declare one custom command without executing it."""
        if deps not in (None, "gcc", "msvc"):
            raise ConfigurationError(f"Unsupported dependency format: {deps}")
        if depfile is not None and deps != "gcc":
            raise ConfigurationError("Custom action depfiles require deps='gcc'")
        return ActionSpec(
            command=tuple(str(value) for value in command),
            outputs=tuple(Path(value) for value in outputs),
            inputs=_inputs(inputs),
            implicit_inputs=_inputs(implicit_inputs),
            order_only=_inputs(order_only),
            environment=dict(environment or {}),
            depfile=Path(depfile) if depfile is not None else None,
            deps=cast(Literal["gcc", "msvc"] | None, deps),
            description=description,
            pool=pool,
            timeout_seconds=timeout_seconds,
            restat=restat,
        )

    def dependency(
        self,
        name: str,
        *,
        include_dirs: Sequence[str | os.PathLike[str]] = (),
        defines: Sequence[str] = (),
        compile_arguments: Sequence[str] = (),
        libraries: Sequence[str | os.PathLike[str]] = (),
        library_dirs: Sequence[str | os.PathLike[str]] = (),
        link_arguments: Sequence[str] = (),
        runtime_files: Sequence[str | os.PathLike[str]] = (),
    ) -> Dependency:
        """Declare a prebuilt or interface-only dependency."""
        return Dependency(
            name=name,
            compile=CompileInterface(
                include_dirs=tuple(Path(value) for value in include_dirs),
                defines=tuple(defines),
                arguments=tuple(compile_arguments),
            ),
            link=LinkInterface(
                libraries=tuple(Path(value) if Path(value).suffix else str(value) for value in libraries),
                library_dirs=tuple(Path(value) for value in library_dirs),
                arguments=tuple(link_arguments),
            ),
            runtime_files=tuple(Path(value) for value in runtime_files),
        )

    prebuilt_library = dependency

    def archive(self, url: str, sha256: str, *, strip_prefix: str | None = None) -> ArchiveSource:
        """Declare an archive source pinned by a lowercase SHA-256 digest."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in ("http", "https") and (parsed.username is not None or parsed.password is not None):
            raise ConfigurationError("Package source URLs cannot contain credentials")
        if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ConfigurationError("Archive sha256 must be 64 lowercase hexadecimal characters")
        return ArchiveSource(url, sha256, strip_prefix)

    def git(self, url: str, revision: str) -> GitSource:
        """Declare a Git source pinned to one full commit hash."""
        parsed = urllib.parse.urlparse(url)
        local_path = Path(url).expanduser()
        is_scp = url.startswith("git@")
        if not local_path.is_absolute() and not is_scp and parsed.scheme not in ("", "file", "https", "ssh"):
            raise ConfigurationError("Git package sources require an HTTPS, SSH, file, or local path URL")
        if parsed.scheme in ("http", "https") and (parsed.username is not None or parsed.password is not None):
            raise ConfigurationError("Package source URLs cannot contain credentials")
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
            raise ConfigurationError("Git revision must be a full lowercase commit hash")
        return GitSource(url, revision)

    def msbuild(
        self,
        project_file: str | os.PathLike[str],
        *,
        kind: TargetKind | None = None,
        defines: Sequence[str] = (),
    ) -> MsbuildProject:
        """Import one upstream Visual C++ project without invoking MSBuild."""
        if kind not in (None, "static_library", "shared_library", "executable"):
            raise ConfigurationError(f"Unsupported MSBuild target kind: {kind}")
        path = _safe_relative(self.root, project_file, must_exist=False)
        return MsbuildProject(path, kind, tuple(defines))

    def package(
        self,
        name: str,
        *,
        source: PackageSource,
        overlay: str | os.PathLike[str] | None = None,
        build: PackageBuild | None = None,
    ) -> PackageRef:
        """Declare one pinned external project without fetching or executing it."""
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None or name in (".", ".."):
            raise ConfigurationError(f"Invalid package name: {name!r}")
        if any(existing.casefold() == name.casefold() for existing in self._packages):
            raise ConfigurationError(f"Duplicate package name: {name}")
        if overlay is not None and build is not None:
            raise ConfigurationError("Package overlay and build cannot both be specified")
        overlay_path = _safe_relative(self.root, overlay, must_exist=True) if overlay is not None else None
        self._packages[name] = PackageSpec(name, source, overlay_path, build)
        return PackageRef(name)

    def public(self, target: TargetRef | PackageTargetRef | PackageRef) -> TargetDependency:
        """Expose a target's compile and link interface to consumers."""
        if isinstance(target, PackageRef):
            target = PackageTargetRef(target.name, "")
        return TargetDependency(target, "public")

    def private(self, target: TargetRef | PackageTargetRef | PackageRef) -> TargetDependency:
        """Use a target without exposing its compile interface to consumers."""
        if isinstance(target, PackageRef):
            target = PackageTargetRef(target.name, "")
        return TargetDependency(target, "private")

    def output(self, target: TargetRef, path: str | os.PathLike[str] | None = None) -> Artifact:
        """Reference one output from a previously declared target."""
        spec = self._targets.get(target.name)
        if spec is None:
            raise ConfigurationError(f"Unknown target: {target.name}")
        if path is None:
            if len(spec.outputs) == 1:
                selected = spec.outputs[0]
            elif not spec.outputs and spec.kind in ("static_library", "shared_library", "executable"):
                selected = Path(spec.name)
            else:
                raise ConfigurationError(f"Target {target.name} requires an explicit output selection")
        else:
            selected = Path(path)
            if selected not in spec.outputs:
                raise ConfigurationError(f"Target {target.name} does not produce {selected.as_posix()}")
        return Artifact(target, selected)

    def _target(
        self,
        name: str,
        kind: TargetKind,
        *,
        sources: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        public_headers: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        private_headers: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        include_dirs: Sequence[str | os.PathLike[str]] = (),
        defines: Sequence[str] = (),
        compile_arguments: Sequence[str] = (),
        link_arguments: Sequence[str] = (),
        dependencies: Sequence[Dependency | TargetDependency] = (),
        objects: Sequence[TargetRef] = (),
        runtime_files: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        outputs: Sequence[str | os.PathLike[str]] = (),
        action: ActionSpec | None = None,
    ) -> TargetRef:
        if not name or any(character.isspace() for character in name):
            raise ConfigurationError(f"Invalid target name: {name!r}")
        if name in self._targets:
            raise ConfigurationError(f"Duplicate target name: {name}")
        spec = TargetSpec(
            name=name,
            kind=kind,
            sources=_inputs(sources),
            public_headers=_inputs(public_headers),
            private_headers=_inputs(private_headers),
            include_dirs=tuple(Path(value) for value in include_dirs),
            defines=tuple(defines),
            compile_arguments=tuple(compile_arguments),
            link_arguments=tuple(link_arguments),
            dependencies=tuple(dependencies),
            objects=tuple(objects),
            runtime_files=_inputs(runtime_files),
            outputs=tuple(Path(value) for value in outputs),
            action=action,
        )
        self._targets[name] = spec
        return TargetRef(name)

    def object_library(self, name: str, **kwargs: Any) -> TargetRef:
        return self._target(name, "object_library", **kwargs)

    def static_library(self, name: str, **kwargs: Any) -> TargetRef:
        return self._target(name, "static_library", **kwargs)

    def shared_library(self, name: str, **kwargs: Any) -> TargetRef:
        return self._target(name, "shared_library", **kwargs)

    def executable(self, name: str, **kwargs: Any) -> TargetRef:
        return self._target(name, "executable", **kwargs)

    def custom_target(self, name: str, action: ActionSpec) -> TargetRef:
        return self._target(name, "custom", outputs=action.outputs, action=action)

    def external_library(
        self,
        name: str,
        action: ActionSpec,
        *,
        include_dirs: Sequence[str | os.PathLike[str]] = (),
        defines: Sequence[str] = (),
        compile_arguments: Sequence[str] = (),
        link_arguments: Sequence[str] = (),
        runtime_files: FileSet | BuildInput | Sequence[BuildInput] | None = None,
    ) -> TargetRef:
        """Declare a library produced by an explicit external build action."""
        return self._target(
            name,
            "external_library",
            outputs=action.outputs,
            action=action,
            include_dirs=include_dirs,
            defines=defines,
            compile_arguments=compile_arguments,
            link_arguments=link_arguments,
            runtime_files=runtime_files,
        )

    def runtime_bundle(
        self,
        name: str,
        files: FileSet | BuildInput | Sequence[BuildInput],
        *,
        destination: str | os.PathLike[str] = ".",
    ) -> TargetRef:
        return self._target(
            name,
            "runtime_bundle",
            runtime_files=files,
            outputs=(Path(destination) / f".{name}.stamp",),
        )

    def alias(self, name: str, targets: Sequence[TargetRef]) -> TargetRef:
        return self._target(name, "alias", objects=targets)

    def command(self, spec: CommandSpec) -> None:
        if any(existing.path == spec.path for existing in self._commands):
            raise ConfigurationError(f"Duplicate command path: {' '.join(spec.path)}")
        self._commands.append(spec)

    def task(self, spec: TaskSpec) -> None:
        if any(existing.name == spec.name for existing in self._tasks):
            raise ConfigurationError(f"Duplicate task: {spec.name}")
        self._tasks.append(spec)

    def pool(self, spec: PoolSpec) -> None:
        """Register a constrained Ninja action pool."""
        if any(existing.name == spec.name for existing in self._pools):
            raise ConfigurationError(f"Duplicate pool: {spec.name}")
        self._pools.append(spec)

    def test(self, spec: TestSpec) -> None:
        """Register a test invocation."""
        if any(existing.name == spec.name for existing in self._tests):
            raise ConfigurationError(f"Duplicate test: {spec.name}")
        self._tests.append(spec)

    def benchmark(self, spec: BenchmarkSpec) -> None:
        """Register a benchmark invocation."""
        if any(existing.name == spec.name for existing in self._benchmarks):
            raise ConfigurationError(f"Duplicate benchmark: {spec.name}")
        self._benchmarks.append(spec)

    def artifact(self, spec: ArtifactSpec) -> None:
        """Register a reproducible package."""
        if any(existing.name == spec.name for existing in self._artifacts):
            raise ConfigurationError(f"Duplicate artifact: {spec.name}")
        self._artifacts.append(spec)

    def release(self, spec: ReleaseSpec) -> None:
        """Register a release policy."""
        if any(existing.name == spec.name for existing in self._releases):
            raise ConfigurationError(f"Duplicate release: {spec.name}")
        self._releases.append(spec)

    def remote(self, spec: RemoteSpec) -> None:
        """Register a remote execution host."""
        if any(existing.name == spec.name for existing in self._remotes):
            raise ConfigurationError(f"Duplicate remote: {spec.name}")
        self._remotes.append(spec)

    def github(self, spec: GitHubSpec) -> None:
        """Configure GitHub release coordinates."""
        if self._github is not None:
            raise ConfigurationError("GitHub configuration is already registered")
        self._github = spec

    def project(self, name: str, *, defaults: Sequence[TargetRef] = ()) -> ProjectSpec:
        return ProjectSpec(
            name=name,
            targets=tuple(self._targets.values()),
            defaults=tuple(defaults),
            packages=tuple(self._packages.values()),
            commands=tuple(self._commands),
            tasks=tuple(self._tasks),
            pools=tuple(self._pools),
            tests=tuple(self._tests),
            benchmarks=tuple(self._benchmarks),
            artifacts=tuple(self._artifacts),
            releases=tuple(self._releases),
            remotes=tuple(self._remotes),
            github=self._github,
        )


def project_root_find(start: Path) -> Path:
    """Find the closest ancestor containing drift.toml."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "drift.toml").is_file():
            return candidate
    raise ConfigurationError(f"No drift.toml found from {start}")


def project_load(root: Path, config: BuildConfig) -> ProjectSpec:
    """Load and evaluate the configured project provider."""
    manifest_path = root / "drift.toml"
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"Cannot read {manifest_path}: {error}") from error
    project = manifest.get("project")
    if not isinstance(project, dict):
        raise ConfigurationError("drift.toml requires a [project] table")
    if project.get("api-version") != 0:
        raise ConfigurationError("drift.toml requires project.api-version = 0")
    provider = project.get("provider")
    if not isinstance(provider, str) or ":" not in provider:
        raise ConfigurationError("project.provider must be 'module:callable'")
    module_name, function_name = provider.split(":", 1)
    sys.path.insert(0, str(root))
    try:
        importlib.invalidate_caches()
        existing = sys.modules.get(module_name)
        existing_file = getattr(existing, "__file__", None)
        if isinstance(existing_file, str):
            try:
                Path(existing_file).resolve().relative_to(root)
            except ValueError:
                sys.modules.pop(module_name, None)
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)
        result = function(ProjectApi(root, config))
    except (ImportError, AttributeError, TypeError) as error:
        raise ConfigurationError(f"Cannot load provider {provider}: {error}") from error
    finally:
        sys.path.pop(0)
    if not isinstance(result, ProjectSpec):
        raise ConfigurationError(f"Provider {provider} did not return ProjectSpec")
    return result


def project_provider_files(root: Path) -> tuple[Path, ...]:
    """Return loaded Python modules owned by the current project."""
    files: set[Path] = {root / "drift.toml"}
    if (root / "drift.lock").is_file():
        files.add(root / "drift.lock")
    for module in tuple(sys.modules.values()):
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            continue
        path = Path(module_file).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.suffix in (".py", ".toml"):
            files.add(path)
    return tuple(sorted(files, key=lambda path: path.as_posix()))
