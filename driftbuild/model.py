"""Immutable public data model used by project providers."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias

TargetKind: TypeAlias = Literal[
    "object_library",
    "static_library",
    "shared_library",
    "executable",
    "custom",
    "external_library",
    "runtime_bundle",
    "alias",
]
Visibility: TypeAlias = Literal["public", "private"]
PackageLinkage: TypeAlias = Literal["auto", "static", "shared"]


@dataclass(frozen=True)
class BuildConfig:
    """Selected platform, toolchain, build type, and provider-defined values."""

    platform: str
    architecture: str = "x86_64"
    compiler: str = "auto"
    build_type: str = "debug"
    values: Mapping[str, str] = field(default_factory=dict)
    target: str | None = None
    sysroot: Path | None = None
    toolchain_file: Path | None = None
    sanitizers: tuple[str, ...] = ()
    coverage: bool = False
    lto: bool = False
    warnings: Literal["default", "all", "error"] = "default"
    unity_size: int = 0
    profile: Literal["host", "android", "ios", "emscripten", "mingw", "clang-cl"] = "host"
    hermetic: bool = False


@dataclass(frozen=True)
class FileSet:
    """Stable collection of repository-relative input files."""

    files: tuple[Path, ...]

    def __iter__(self) -> Iterator[Path]:
        return iter(self.files)


@dataclass(frozen=True)
class TargetRef:
    """Stable reference to a target declared in the current project."""

    name: str


@dataclass(frozen=True)
class PackageTargetRef:
    """Reference to one exported target from a declared package."""

    package: str
    target: str


@dataclass(frozen=True)
class PackageRef:
    """Provider-facing handle for selecting targets from one package."""

    name: str

    def target(self, name: str) -> PackageTargetRef:
        """Return a typed reference to one target exported by this package."""
        return PackageTargetRef(self.name, name)

    component = target


@dataclass(frozen=True)
class ArchiveSource:
    """Immutable archive source verified by SHA-256."""

    url: str
    sha256: str
    strip_prefix: str | None = None


@dataclass(frozen=True)
class GitSource:
    """Git source pinned to one exact commit revision."""

    url: str
    revision: str
    submodules: bool = False
    track: str | None = None


@dataclass(frozen=True)
class VcpkgSource:
    """One vcpkg port resolved against an exact registry baseline."""

    port: str
    baseline: str
    registry: str = "https://github.com/microsoft/vcpkg"
    features: tuple[str, ...] = ()


PackageSource: TypeAlias = ArchiveSource | GitSource | VcpkgSource


@dataclass(frozen=True)
class MsbuildProject:
    """An upstream Visual C++ project imported as a Drift target."""

    project_file: Path
    kind: TargetKind | None = None
    defines: tuple[str, ...] = ()


PackageBuild: TypeAlias = MsbuildProject


@dataclass(frozen=True)
class PackageSpec:
    """Pinned external project and its optional build description."""

    name: str
    source: PackageSource
    overlay: Path | None = None
    build: PackageBuild | None = None
    options: tuple[tuple[str, str], ...] = ()
    features: tuple[str, ...] = ()
    patches: tuple[Path, ...] = ()
    adapter: str | None = None
    components: tuple[str, ...] = ()
    linkage: PackageLinkage = "auto"


@dataclass(frozen=True)
class Artifact:
    """Reference to one declared output of another target."""

    target: TargetRef
    path: Path


BuildInput: TypeAlias = Path | Artifact


@dataclass(frozen=True)
class CompileInterface:
    """Compile requirements contributed by a dependency."""

    include_dirs: tuple[Path, ...] = ()
    defines: tuple[str, ...] = ()
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class LinkInterface:
    """Link requirements contributed by a dependency."""

    libraries: tuple[str | Path, ...] = ()
    library_dirs: tuple[Path, ...] = ()
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class Dependency:
    """Prebuilt or interface-only compile, link, and runtime dependency."""

    name: str
    compile: CompileInterface = CompileInterface()
    link: LinkInterface = LinkInterface()
    runtime_files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class TargetDependency:
    """Target dependency with explicit public or private visibility."""

    target: TargetRef | PackageTargetRef
    visibility: Visibility


@dataclass(frozen=True)
class ActionSpec:
    """Command with explicit inputs, outputs, environment, and execution policy."""

    command: tuple[str, ...]
    outputs: tuple[Path, ...]
    inputs: tuple[BuildInput, ...] = ()
    implicit_inputs: tuple[BuildInput, ...] = ()
    order_only: tuple[BuildInput, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    depfile: Path | None = None
    deps: Literal["gcc", "msvc"] | None = None
    description: str | None = None
    pool: str | None = None
    timeout_seconds: float | None = None
    restat: bool = False


@dataclass(frozen=True)
class TargetSpec:
    """One typed target in a provider-declared build graph."""

    name: str
    kind: TargetKind
    sources: tuple[BuildInput, ...] = ()
    public_headers: tuple[BuildInput, ...] = ()
    private_headers: tuple[BuildInput, ...] = ()
    include_dirs: tuple[Path, ...] = ()
    defines: tuple[str, ...] = ()
    compile_arguments: tuple[str, ...] = ()
    link_arguments: tuple[str, ...] = ()
    dependencies: tuple[Dependency | TargetDependency, ...] = ()
    objects: tuple[TargetRef, ...] = ()
    runtime_files: tuple[BuildInput, ...] = ()
    outputs: tuple[Path, ...] = ()
    action: ActionSpec | None = None
    precompiled_header: Path | None = None


@dataclass(frozen=True)
class OptionSpec:
    """Typed command-line option decoded before invoking a provider handler."""

    name: str
    value_type: type[str] | type[int] | type[float] = str
    default: str | int | float | bool | None = None
    choices: tuple[str, ...] = ()
    flag: bool = False
    required: bool = False
    secret: bool = False
    help: str = ""


@dataclass(frozen=True)
class CommandResult:
    """Structured result from a provider command."""

    exit_code: int = 0
    message: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)


class CommandHandler(Protocol):
    def __call__(self, context: CommandContext, options: Any) -> CommandResult | int | None: ...


@dataclass(frozen=True)
class CommandSpec:
    """Provider command with typed options and a sync or async handler."""

    path: tuple[str, ...]
    help: str
    handler: Callable[..., Any]
    options: tuple[OptionSpec, ...] = ()
    options_type: type[Any] | None = None


@dataclass(frozen=True)
class TaskSpec:
    """Workflow task with dependencies, retries, resources, and timeout."""

    name: str
    command: tuple[str, ...] | None = None
    handler: Callable[..., Any] | None = None
    dependencies: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    retries: int = 0


@dataclass(frozen=True)
class PoolSpec:
    """Named Ninja concurrency pool for constrained custom actions."""

    name: str
    depth: int


@dataclass(frozen=True)
class TestSpec:
    """Test invocation with labels, timeout, and optional build prerequisite."""

    name: str
    command: tuple[str, ...]
    labels: tuple[str, ...] = ()
    build_targets: tuple[TargetRef, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    working_directory: Path | None = None


@dataclass(frozen=True)
class BenchmarkSpec:
    """Benchmark invocation with repeat and warmup policy."""

    name: str
    command: tuple[str, ...]
    build_targets: tuple[TargetRef, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    warmups: int = 1
    repetitions: int = 5
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class ArtifactSpec:
    """Named package assembled from declared files or target outputs."""

    name: str
    files: tuple[BuildInput, ...]
    format: Literal["zip", "tar.gz"] = "zip"
    prefix: str = ""


@dataclass(frozen=True)
class ReleaseSpec:
    """Release policy connecting version, artifacts, and optional publication."""

    name: str
    version: str
    artifacts: tuple[str, ...]
    tag: str | None = None
    draft: bool = True


@dataclass(frozen=True)
class RemoteSpec:
    """Remote host used by provider commands for bounded command execution."""

    name: str
    host: str
    user: str | None = None
    port: int = 22
    identity_file: Path | None = None


@dataclass(frozen=True)
class GitHubSpec:
    """GitHub repository coordinates used by release automation."""

    repository: str
    remote: str = "origin"


@dataclass(frozen=True)
class ProjectSpec:
    """Complete provider declaration returned to Drift."""

    name: str
    targets: tuple[TargetSpec, ...] = ()
    defaults: tuple[TargetRef, ...] = ()
    packages: tuple[PackageSpec, ...] = ()
    commands: tuple[CommandSpec, ...] = ()
    tasks: tuple[TaskSpec, ...] = ()
    pools: tuple[PoolSpec, ...] = ()
    tests: tuple[TestSpec, ...] = ()
    benchmarks: tuple[BenchmarkSpec, ...] = ()
    artifacts: tuple[ArtifactSpec, ...] = ()
    releases: tuple[ReleaseSpec, ...] = ()
    remotes: tuple[RemoteSpec, ...] = ()
    github: GitHubSpec | None = None


@dataclass(frozen=True)
class CommandContext:
    """Services and repository state injected into provider commands."""

    project_root: Path
    state_root: Path
    environment: Mapping[str, str]
    verbose: bool = False
