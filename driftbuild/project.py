"""Provider-facing declarations and project loading."""

from __future__ import annotations

import importlib
import os
import re
import stat
import sys
import tomllib
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import replace
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
    CommandGroupSpec,
    CommandSpec,
    CompileInterface,
    Dependency,
    Deployment,
    FileSet,
    GitHubSpec,
    GitSource,
    LinkInterface,
    MatrixSpec,
    MsbuildProject,
    PackageBuild,
    PackageLinkage,
    PackageRef,
    PackageSource,
    PackageSpec,
    PackageTargetRef,
    PoolSpec,
    ProjectOptionSpec,
    ProjectSpec,
    ReleaseSpec,
    RemoteSpec,
    RuntimeInput,
    SuiteSpec,
    TargetDependency,
    TargetKind,
    TargetRef,
    TargetSpec,
    TaskSpec,
    TestSpec,
    VcpkgSource,
)
from driftbuild.runtime import module_command

API_VERSION = 1
SUPPORTED_API_VERSIONS = frozenset({0, API_VERSION})


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


def _runtime_inputs(
    values: FileSet | RuntimeInput | Sequence[RuntimeInput] | None,
) -> tuple[RuntimeInput, ...]:
    if values is None:
        return ()
    if isinstance(values, FileSet):
        return tuple(values.files)
    if isinstance(values, (Path, Artifact, Deployment)):
        return (values,)
    return tuple(values)


class ProjectApi:
    """Builds one immutable project declaration for a selected configuration."""

    def __init__(self, root: Path, config: BuildConfig, api_version: int = API_VERSION):
        self.root = root.resolve()
        self.config = config
        self.api_version = api_version
        self._targets: dict[str, TargetSpec] = {}
        self._packages: dict[str, PackageSpec] = {}
        self._commands: list[CommandSpec] = []
        self._command_groups: list[CommandGroupSpec] = []
        self._tasks: list[TaskSpec] = []
        self._pools: list[PoolSpec] = []
        self._options: list[ProjectOptionSpec] = []
        self._option_values: dict[str, str] = {}
        self._tests: list[TestSpec] = []
        self._suites: list[SuiteSpec] = []
        self._matrices: list[MatrixSpec] = []
        self._benchmarks: list[BenchmarkSpec] = []
        self._artifacts: list[ArtifactSpec] = []
        self._releases: list[ReleaseSpec] = []
        self._remotes: list[RemoteSpec] = []
        self._github: GitHubSpec | None = None
        self._discovery_directories: set[Path] = set()
        self._configuration_inputs: set[Path] = set()
        self._configuration_environment: set[str] = set()

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
        for pattern in (*include, *exclude):
            pattern_path = Path(pattern)
            if pattern_path.is_absolute() or ".." in pattern_path.parts:
                raise ConfigurationError(f"Tree pattern escapes its root: {pattern}")

        for directory, names, _files in os.walk(absolute_root, followlinks=False):
            current = Path(directory)
            self._discovery_directories.add(current)
            names[:] = [name for name in names if not (current / name).is_symlink()]

        found: dict[str, Path] = {}
        excluded: set[Path] = set()
        root_part_count = len(self.root.parts)
        for pattern in exclude:
            excluded.update(absolute_root.glob(pattern))
        for pattern in include:
            for path in absolute_root.glob(pattern):
                try:
                    is_file = stat.S_ISREG(path.lstat().st_mode)
                except OSError:
                    continue
                if path in excluded or not is_file:
                    continue
                relative = Path(*path.parts[root_part_count:])
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
        stamp_outputs: bool = False,
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
            stamp_outputs=stamp_outputs,
        )

    def provider_action(
        self,
        handler: str,
        arguments: Sequence[str] = (),
        *,
        outputs: Sequence[str | os.PathLike[str]],
        inputs: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        implicit_inputs: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        order_only: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        environment: Mapping[str, str] | None = None,
        description: str | None = None,
        pool: str | None = None,
        timeout_seconds: float | None = None,
        restat: bool = False,
        stamp_outputs: bool = False,
    ) -> ActionSpec:
        """Declare an importable provider handler executed by Drift's bundled runtime."""
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*", handler) is None:
            raise ConfigurationError(f"Provider handler must be 'module:function': {handler!r}")
        module_name = handler.partition(":")[0]
        module_path = self.root.joinpath(*module_name.split(".")).with_suffix(".py")
        if not module_path.is_file():
            package_path = self.root.joinpath(*module_name.split("."), "__init__.py")
            module_path = package_path if package_path.is_file() else module_path
        if not module_path.is_file():
            raise ConfigurationError(f"Provider handler module does not exist: {module_name}")
        tracked_inputs = (*_inputs(implicit_inputs), module_path.resolve())
        return ActionSpec(
            command=tuple(str(value) for value in arguments),
            outputs=tuple(Path(value) for value in outputs),
            inputs=_inputs(inputs),
            implicit_inputs=tuple(dict.fromkeys(tracked_inputs)),
            order_only=_inputs(order_only),
            environment=dict(environment or {}),
            description=description,
            pool=pool,
            timeout_seconds=timeout_seconds,
            restat=restat,
            stamp_outputs=stamp_outputs,
            handler=handler,
        )

    def option(
        self,
        name: str,
        *,
        value_type: type[str] | type[int] | type[float] | type[bool] = str,
        default: str | int | float | bool | None = None,
        choices: Sequence[str] = (),
        help: str = "",
    ) -> Any:
        """Declare, validate, and return one typed provider configuration value."""
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", name) is None:
            raise ConfigurationError(f"Invalid project option name: {name!r}")
        if any(existing.name == name for existing in self._options):
            raise ConfigurationError(f"Duplicate project option: {name}")
        if value_type not in (str, int, float, bool):
            raise ConfigurationError(f"Unsupported project option type for {name}: {value_type}")
        if default is not None and type(default) is not value_type:
            raise ConfigurationError(f"Project option {name} default must be {value_type.__name__}")
        if choices and value_type is not str:
            raise ConfigurationError(f"Project option {name} choices require a string value type")
        raw = self.config.values.get(name)
        value: Any = default
        if raw is not None:
            try:
                if value_type is bool:
                    lowered = raw.casefold()
                    if lowered not in ("true", "false"):
                        raise ValueError
                    value = lowered == "true"
                else:
                    value = value_type(raw)
            except ValueError as error:
                raise ConfigurationError(f"Project option {name} has invalid {value_type.__name__} value: {raw!r}") from error
        if value is None:
            raise ConfigurationError(f"Project option {name} requires a value")
        if choices and str(value) not in choices:
            raise ConfigurationError(f"Project option {name} must be one of: {', '.join(choices)}")
        self._options.append(ProjectOptionSpec(name, value_type, default, tuple(choices), help))
        self._option_values[name] = str(value).lower() if isinstance(value, bool) else str(value)
        return value

    def local_sdk(
        self,
        name: str,
        *,
        descriptor: str | os.PathLike[str],
        environment: Sequence[str] = (),
        roots: Sequence[str | os.PathLike[str] | None] = (),
    ) -> Dependency:
        """Import a manifest-described local SDK from the first available root."""
        from driftbuild.sdk import local_sdk_load

        descriptor_path = self.root / _safe_relative(self.root, descriptor, must_exist=True)
        self._configuration_inputs.add(descriptor_path)
        self._configuration_environment.update(environment)
        candidates = [Path(value).expanduser() for variable in environment if (value := os.environ.get(variable))]
        candidates.extend(Path(value).expanduser() for value in roots if value is not None)
        resolved = [path.resolve() if path.is_absolute() else (self.root / path).resolve() for path in candidates]
        selected = next((path for path in resolved if path.is_dir()), None)
        if selected is None:
            detail = ", ".join(str(path) for path in resolved) or "no roots configured"
            raise ConfigurationError(f"Local SDK {name} was not found: {detail}")
        selected_config = replace(self.config, values={**self.config.values, **self._option_values})
        return local_sdk_load(name, selected, descriptor_path, self.root, selected_config)

    def _cargo_action(
        self,
        name: str,
        *,
        manifest: str | os.PathLike[str],
        workspace: bool = False,
        packages: Sequence[str] = (),
        targets: Sequence[str] = (),
        features: Sequence[str] = (),
        all_features: bool = False,
        no_default_features: bool = False,
        arguments: Sequence[str] = (),
        environment: Mapping[str, str] | None = None,
        outputs: Sequence[str | os.PathLike[str]] = (),
        inputs: FileSet | BuildInput | Sequence[BuildInput] | None = None,
        run_target: str | None = None,
        target_directory: str | os.PathLike[str] | None = None,
        artifact_kind: Literal["bin", "staticlib"] | None = None,
        artifact_names: Sequence[str] = (),
    ) -> ActionSpec:
        manifest_path = _safe_relative(self.root, manifest, must_exist=True)
        if manifest_path.name != "Cargo.toml":
            raise ConfigurationError("Cargo manifests must be named Cargo.toml")
        if workspace and packages:
            raise ConfigurationError("Cargo targets cannot select both a workspace and packages")
        if any(not value or value.startswith("-") for value in (*packages, *targets, *features)):
            raise ConfigurationError("Cargo package, target, and feature names cannot be empty or options")

        cargo_inputs = inputs
        if cargo_inputs is None:
            cargo_inputs = self.tree(
                manifest_path.parent,
                include=("**/*.rs", "**/Cargo.toml", "Cargo.lock", "**/build.rs", ".cargo/**/*.toml"),
                exclude=("target/**/*", ".drift/**/*"),
            )

        cargo_target_directory = (
            "{build}"
            if target_directory is None
            else str(self.root / _safe_relative(self.root, target_directory, must_exist=False))
        )
        selected_artifacts = tuple(artifact_names)
        if not selected_artifacts and targets:
            selected_artifacts = tuple(targets)
            artifact_kind = artifact_kind or "bin"
        if run_target is not None and not selected_artifacts:
            selected_artifacts = (run_target,)
            artifact_kind = "bin"
        if selected_artifacts and artifact_kind is None:
            artifact_kind = "bin"
        if any(not value or value.startswith("-") for value in selected_artifacts):
            raise ConfigurationError("Cargo artifact names cannot be empty or options")

        declared_outputs = tuple(Path(value) for value in outputs)
        if not declared_outputs and selected_artifacts:
            if artifact_kind == "bin":
                suffix = ".exe" if self.config.platform == "win32" else ""
                declared_outputs = tuple(
                    Path("cargo-artifacts") / name / f"{artifact}{suffix}" for artifact in selected_artifacts
                )
            else:
                prefix = "" if self.config.platform == "win32" else "lib"
                suffix = ".lib" if self.config.platform == "win32" else ".a"
                declared_outputs = tuple(
                    Path("cargo-artifacts") / name / f"{prefix}{artifact.replace('-', '_')}{suffix}"
                    for artifact in selected_artifacts
                )
        if selected_artifacts and len(declared_outputs) != len(selected_artifacts):
            raise ConfigurationError("Cargo artifact and output counts must match")

        command = [
            *module_command("driftbuild.cargo"),
            "--manifest",
            manifest_path.as_posix(),
            "--target-dir",
            cargo_target_directory,
        ]
        if self.config.build_type == "release":
            command.append("--release")
        if workspace:
            command.append("--workspace")
        for package in packages:
            command.extend(("--package", package))
        for target in targets:
            command.extend(("--bin", target))
        if features:
            command.extend(("--features", ",".join(features)))
        if all_features:
            command.append("--all-features")
        if no_default_features:
            command.append("--no-default-features")
        for index, artifact in enumerate(selected_artifacts):
            command.extend(("--artifact", f"{artifact_kind}:{artifact}", "--output", f"{{out:{index}}}"))
        if arguments:
            command.append("--")
            command.extend(str(value) for value in arguments)

        stamp_outputs = not declared_outputs
        if stamp_outputs:
            declared_outputs = (Path("cargo-stamps") / f"{name}.stamp",)
        action = self.command_action(
            command,
            outputs=declared_outputs,
            inputs=cargo_inputs,
            environment=environment,
            description=f"CARGO {name}",
            restat=True,
            stamp_outputs=stamp_outputs,
        )
        return action

    def cargo(self, name: str, **kwargs: Any) -> TargetRef:
        """Declare a Cargo build owned and incrementally scheduled by Drift."""
        run_target = kwargs.get("run_target")
        if run_target is not None:
            kwargs.setdefault("artifact_kind", "bin")
        action = self._cargo_action(name, **kwargs)
        if run_target is None:
            return self.custom_target(name, action)
        return self._target(
            name,
            "custom",
            outputs=action.outputs,
            action=action,
            run_command=("{out}",),
            run_environment=kwargs.get("environment"),
        )

    def cargo_static_library(
        self,
        name: str,
        *,
        include_dirs: Sequence[str | os.PathLike[str]] = (),
        defines: Sequence[str] = (),
        compile_arguments: Sequence[str] = (),
        link_arguments: Sequence[str] = (),
        runtime_files: FileSet | RuntimeInput | Sequence[RuntimeInput] | None = None,
        artifact_name: str | None = None,
        **kwargs: Any,
    ) -> TargetRef:
        """Declare a Cargo static library consumed by native Drift targets."""
        arguments = tuple(kwargs.pop("arguments", ()))
        if "--lib" not in arguments:
            arguments = (*arguments, "--lib")
        action = self._cargo_action(
            name,
            artifact_kind="staticlib",
            artifact_names=(artifact_name or name,),
            arguments=arguments,
            **kwargs,
        )
        return self.external_library(
            name,
            action,
            include_dirs=include_dirs,
            defines=defines,
            compile_arguments=compile_arguments,
            link_arguments=link_arguments,
            runtime_files=runtime_files,
        )

    def cargo_workspace(
        self,
        name: str,
        *,
        manifest: str | os.PathLike[str],
        checks: Sequence[str] = ("format", "check", "clippy", "test"),
        timeout_seconds: float | None = None,
    ) -> tuple[TestSpec, ...]:
        """Register the conventional validation commands for one Cargo workspace."""
        manifest_path = _safe_relative(self.root, manifest, must_exist=True)
        supported = {"format", "check", "clippy", "test"}
        unknown = set(checks) - supported
        if unknown:
            raise ConfigurationError(f"Unknown Cargo workspace checks: {', '.join(sorted(unknown))}")
        commands = {
            "format": ("cargo", "fmt", "--manifest-path", manifest_path.as_posix(), "--all", "--", "--check"),
            "check": ("cargo", "check", "--manifest-path", manifest_path.as_posix(), "--workspace", "--all-targets"),
            "clippy": ("cargo", "clippy", "--manifest-path", manifest_path.as_posix(), "--workspace", "--all-targets"),
            "test": ("cargo", "test", "--manifest-path", manifest_path.as_posix(), "--workspace", "--all-targets"),
        }
        declared = []
        for check in checks:
            test_name = name if check == "test" else f"{name}-{check}"
            spec = TestSpec(
                test_name,
                commands[check],
                labels=("rust", name),
                timeout_seconds=timeout_seconds,
            )
            self.test(spec)
            declared.append(spec)
        return tuple(declared)

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
        runtime_files: FileSet | RuntimeInput | Sequence[RuntimeInput] | None = None,
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
            runtime_files=_runtime_inputs(runtime_files),
        )

    prebuilt_library = dependency

    def pkg_config(self, name: str, *, static: bool = False) -> Dependency:
        """Import an installed dependency interface through pkg-config."""
        if not name or any(character.isspace() for character in name):
            raise ConfigurationError(f"Invalid pkg-config package name: {name!r}")
        from driftbuild.pkgconfig import dependency_resolve

        return dependency_resolve(name, static=static)

    def archive(self, url: str, sha256: str, *, strip_prefix: str | None = None) -> ArchiveSource:
        """Declare an archive source pinned by a lowercase SHA-256 digest."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in ("http", "https") and (parsed.username is not None or parsed.password is not None):
            raise ConfigurationError("Package source URLs cannot contain credentials")
        if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ConfigurationError("Archive sha256 must be 64 lowercase hexadecimal characters")
        return ArchiveSource(url, sha256, strip_prefix)

    def git(self, url: str, revision: str, *, submodules: bool = False, track: str | None = None) -> GitSource:
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
        if track is not None and (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", track) is None
            or ".." in track
            or "//" in track
            or track.endswith(("/", "."))
        ):
            raise ConfigurationError(f"Invalid Git tracking ref: {track!r}")
        return GitSource(url, revision, submodules, track)

    def vcpkg_source(
        self,
        port: str,
        baseline: str,
        *,
        registry: str = "https://github.com/microsoft/vcpkg",
        features: Sequence[str] = (),
    ) -> VcpkgSource:
        """Declare one vcpkg port against an exact registry baseline."""
        if re.fullmatch(r"[a-z0-9][a-z0-9-]*", port) is None:
            raise ConfigurationError(f"Invalid vcpkg port name: {port!r}")
        if re.fullmatch(r"[0-9a-f]{40}", baseline) is None:
            raise ConfigurationError("vcpkg baseline must be a full lowercase Git commit hash")
        if any(re.fullmatch(r"[a-z0-9][a-z0-9-]*", feature) is None for feature in features):
            raise ConfigurationError("vcpkg features must use lowercase port identifiers")
        return VcpkgSource(port, baseline, registry, tuple(sorted(set(features))))

    vcpkg = vcpkg_source

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
        options: Mapping[str, str | bool | int] | None = None,
        features: Sequence[str] = (),
        patches: Sequence[str | os.PathLike[str]] = (),
        adapter: str | None = None,
        components: Sequence[str] = (),
        linkage: PackageLinkage = "auto",
    ) -> PackageRef:
        """Declare one pinned external project without fetching or executing it."""
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None or name in (".", ".."):
            raise ConfigurationError(f"Invalid package name: {name!r}")
        if any(existing.casefold() == name.casefold() for existing in self._packages):
            raise ConfigurationError(f"Duplicate package name: {name}")
        if overlay is not None and build is not None:
            raise ConfigurationError("Package overlay and build cannot both be specified")
        supported_adapters = {
            "autotools",
            "b2",
            "cmake",
            "conan",
            "make",
            "meson",
            "msbuild",
            "prebuilt",
            "scons",
            "vcpkg",
        }
        if adapter is not None and adapter not in supported_adapters:
            raise ConfigurationError(f"Unknown package adapter: {adapter}")
        if linkage not in ("auto", "static", "shared"):
            raise ConfigurationError(f"Unknown package linkage: {linkage}")
        overlay_path = _safe_relative(self.root, overlay, must_exist=True) if overlay is not None else None
        normalized_options = tuple(
            sorted(
                (key, str(value).lower() if isinstance(value, bool) else str(value))
                for key, value in (options or {}).items()
            )
        )
        if any(re.fullmatch(r"[A-Za-z0-9_.-]+", key) is None for key, _value in normalized_options):
            raise ConfigurationError("Package option names contain invalid characters")
        normalized_features = tuple(sorted(set(features)))
        if any(re.fullmatch(r"[A-Za-z0-9_.-]+", feature) is None for feature in normalized_features):
            raise ConfigurationError("Package feature names contain invalid characters")
        normalized_components = tuple(dict.fromkeys(components))
        if any(re.fullmatch(r"[A-Za-z0-9_.:+-]+", component) is None for component in normalized_components):
            raise ConfigurationError("Package component names contain invalid characters")
        patch_paths = tuple(_safe_relative(self.root, patch, must_exist=True) for patch in patches)
        self._packages[name] = PackageSpec(
            name,
            source,
            overlay_path,
            build,
            normalized_options,
            normalized_features,
            patch_paths,
            adapter,
            normalized_components,
            linkage,
        )
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
        runtime_files: FileSet | RuntimeInput | Sequence[RuntimeInput] | None = None,
        outputs: Sequence[str | os.PathLike[str]] = (),
        action: ActionSpec | None = None,
        precompiled_header: str | os.PathLike[str] | None = None,
        run_command: Sequence[str] = (),
        run_environment: Mapping[str, str] | None = None,
        run_working_directory: str | os.PathLike[str] | None = None,
        runtime_clean: bool = False,
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
            runtime_files=_runtime_inputs(runtime_files),
            outputs=tuple(Path(value) for value in outputs),
            action=action,
            precompiled_header=(
                _safe_relative(self.root, precompiled_header, must_exist=True)
                if precompiled_header is not None
                else None
            ),
            run_command=tuple(str(value) for value in run_command),
            run_environment=dict(run_environment or {}),
            run_working_directory=(
                _safe_relative(self.root, run_working_directory, must_exist=True)
                if run_working_directory is not None
                else None
            ),
            runtime_clean=runtime_clean,
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
        runtime_files: FileSet | RuntimeInput | Sequence[RuntimeInput] | None = None,
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
        files: FileSet | RuntimeInput | Sequence[RuntimeInput],
        *,
        destination: str | os.PathLike[str] = ".",
        clean: bool = True,
    ) -> TargetRef:
        return self._target(
            name,
            "runtime_bundle",
            runtime_files=files,
            outputs=(Path(destination) / f".{name}.stamp",),
            runtime_clean=clean,
        )

    def deploy(
        self,
        source: str | os.PathLike[str] | Artifact,
        destination: str | os.PathLike[str],
    ) -> Deployment:
        """Map one runtime input to a relative path inside a runtime bundle."""
        resolved_source: BuildInput = (
            source
            if isinstance(source, Artifact)
            else Path(source).resolve()
            if Path(source).is_absolute() and Path(source).is_file()
            else _safe_relative(self.root, source, must_exist=True)
        )
        target = Path(destination)
        if target.is_absolute() or ".." in target.parts or target in (Path(""), Path(".")):
            raise ConfigurationError(f"Runtime destination must be a relative file path: {target}")
        return Deployment(resolved_source, target)

    def deploy_tree(
        self,
        files: FileSet,
        source_root: str | os.PathLike[str],
        destination: str | os.PathLike[str] = ".",
    ) -> tuple[Deployment, ...]:
        """Map a deterministic file tree while preserving paths below its source root."""
        relative_root = _safe_relative(self.root, source_root, must_exist=True)
        destination_root = Path(destination)
        if destination_root.is_absolute() or ".." in destination_root.parts:
            raise ConfigurationError(f"Runtime tree destination must be relative: {destination_root}")
        deployments: list[Deployment] = []
        for source in files:
            try:
                relative = source.relative_to(relative_root)
            except ValueError as error:
                raise ConfigurationError(f"Runtime tree input {source} is outside {relative_root}") from error
            deployments.append(self.deploy(source, destination_root / relative))
        return tuple(deployments)

    def alias(self, name: str, targets: Sequence[TargetRef]) -> TargetRef:
        return self._target(name, "alias", objects=targets)

    def command(self, spec: CommandSpec) -> None:
        if not spec.path or any(not part or any(character.isspace() for character in part) for part in spec.path):
            raise ConfigurationError("Command paths require non-empty words")
        if any(existing.path == spec.path for existing in self._commands):
            raise ConfigurationError(f"Duplicate command path: {' '.join(spec.path)}")
        self._commands.append(spec)

    def command_group(self, spec: CommandGroupSpec) -> None:
        """Register a documented branch in the provider command tree."""
        if not spec.path or any(not part or any(character.isspace() for character in part) for part in spec.path):
            raise ConfigurationError("Command group paths require non-empty words")
        if any(existing.path == spec.path for existing in self._command_groups):
            raise ConfigurationError(f"Duplicate command group path: {' '.join(spec.path)}")
        self._command_groups.append(spec)

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

    def suite(self, spec: SuiteSpec) -> None:
        """Register a dependency-aware test suite."""
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", spec.name) is None:
            raise ConfigurationError(f"Invalid suite name: {spec.name!r}")
        if any(existing.name == spec.name for existing in self._suites):
            raise ConfigurationError(f"Duplicate suite: {spec.name}")
        self._suites.append(spec)

    def matrix(self, spec: MatrixSpec) -> None:
        """Register a named Cartesian configuration matrix."""
        if any(existing.name == spec.name for existing in self._matrices):
            raise ConfigurationError(f"Duplicate matrix: {spec.name}")
        if not spec.axes or any(not name or not values for name, values in spec.axes):
            raise ConfigurationError("Matrices require non-empty axes and values")
        if len({name for name, _values in spec.axes}) != len(spec.axes):
            raise ConfigurationError("Matrix axis names must be unique")
        if spec.operation not in ("build", "test"):
            raise ConfigurationError(f"Unsupported matrix operation: {spec.operation}")
        self._matrices.append(spec)

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
            command_groups=tuple(self._command_groups),
            tasks=tuple(self._tasks),
            pools=tuple(self._pools),
            tests=tuple(self._tests),
            suites=tuple(self._suites),
            matrices=tuple(self._matrices),
            benchmarks=tuple(self._benchmarks),
            artifacts=tuple(self._artifacts),
            releases=tuple(self._releases),
            remotes=tuple(self._remotes),
            github=self._github,
            discovery_directories=tuple(sorted(self._discovery_directories, key=lambda path: path.as_posix())),
            options=tuple(self._options),
            configuration_inputs=tuple(sorted(self._configuration_inputs, key=lambda path: path.as_posix())),
            configuration_environment=tuple(sorted(self._configuration_environment)),
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
    from driftbuild.version_requirement import project_requirement_validate

    project_requirement_validate(root)
    manifest_path = root / "drift.toml"
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"Cannot read {manifest_path}: {error}") from error
    project = manifest.get("project")
    if not isinstance(project, dict):
        raise ConfigurationError("drift.toml requires a [project] table")
    api_version = project.get("api-version")
    if not isinstance(api_version, int) or isinstance(api_version, bool):
        raise ConfigurationError("project.api-version must be an integer")
    if api_version not in SUPPORTED_API_VERSIONS:
        supported = ", ".join(str(value) for value in sorted(SUPPORTED_API_VERSIONS))
        raise ConfigurationError(f"Unsupported project.api-version {api_version}; this Drift supports: {supported}")
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
        result = function(ProjectApi(root, config, api_version))
    except (ImportError, AttributeError, TypeError) as error:
        raise ConfigurationError(f"Cannot load provider {provider}: {error}") from error
    finally:
        sys.path.pop(0)
    if not isinstance(result, ProjectSpec):
        raise ConfigurationError(f"Provider {provider} did not return ProjectSpec")
    if result.options:
        unknown = sorted(set(config.values) - {option.name for option in result.options})
        if unknown:
            raise ConfigurationError(f"Unknown project options: {', '.join(unknown)}")
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
        if path.is_file() and path.suffix in (".py", ".toml"):
            files.add(path)
    return tuple(sorted(files, key=lambda path: path.as_posix()))
