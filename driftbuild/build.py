"""Public build service backed by generated Ninja files."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from driftbuild import __version__
from driftbuild.bootstrap import ninja_resolve
from driftbuild.model import BuildConfig, ProjectSpec
from driftbuild.ninja import GeneratedBuild, generate
from driftbuild.process import run
from driftbuild.project import project_provider_files
from driftbuild.toolchain import Toolchain, toolchain_resolve


@dataclass(frozen=True)
class BuildPhaseTiming:
    """Accumulated Ninja job time for one build phase."""

    name: str
    duration_seconds: float
    steps: int


@dataclass(frozen=True)
class BuildTiming:
    """Wall-clock build timing and accumulated Ninja job timings."""

    total_seconds: float
    configure_seconds: float
    ninja_seconds: float
    phases: tuple[BuildPhaseTiming, ...]


@dataclass(frozen=True)
class BuildResult:
    """Resolved build paths, backend state, and optional execution timing."""

    generated: GeneratedBuild
    toolchain: Toolchain
    timing: BuildTiming | None = None


@dataclass(frozen=True)
class _NinjaLogEntry:
    start_milliseconds: int
    end_milliseconds: int
    output: str
    command_hash: str


@dataclass(frozen=True)
class _NinjaLogSnapshot:
    size: int
    tail: bytes


def _ninja_log_snapshot(path: Path) -> _NinjaLogSnapshot:
    if not path.is_file():
        return _NinjaLogSnapshot(0, b"")
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - 128))
        return _NinjaLogSnapshot(size, stream.read())


def _ninja_log_entries(path: Path, snapshot: _NinjaLogSnapshot) -> tuple[_NinjaLogEntry, ...]:
    if not path.is_file() or path.stat().st_size < snapshot.size:
        return ()
    with path.open("rb") as stream:
        stream.seek(snapshot.size - len(snapshot.tail))
        if stream.read(len(snapshot.tail)) != snapshot.tail:
            return ()
        stream.seek(snapshot.size)
        content = stream.read().decode("utf-8")
    entries: list[_NinjaLogEntry] = []
    for line in content.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 5:
            continue
        try:
            entries.append(_NinjaLogEntry(int(fields[0]), int(fields[1]), fields[3], fields[4]))
        except ValueError:
            continue
    return tuple(entries)


def _path_key(path: Path | str) -> str:
    return str(Path(path).resolve()).replace("\\", "/").casefold()


def _phase_timings(entries: tuple[_NinjaLogEntry, ...], output_phases: dict[str, str]) -> tuple[BuildPhaseTiming, ...]:
    edge_phases: dict[tuple[int, int, str], str] = {}
    for entry in entries:
        phase = output_phases.get(_path_key(entry.output))
        if phase is not None:
            edge_phases[(entry.start_milliseconds, entry.end_milliseconds, entry.command_hash)] = phase

    phases: list[BuildPhaseTiming] = []
    for name in ("compile", "archive", "link", "action"):
        edges = [edge for edge, phase in edge_phases.items() if phase == name]
        if edges:
            duration = sum(end - start for start, end, _command_hash in edges) / 1000
            phases.append(BuildPhaseTiming(name, duration, len(edges)))
    return tuple(phases)


def build_timing_render(timing: BuildTiming) -> str:
    """Render one concise build timing summary."""
    values = [
        f"total {timing.total_seconds:.3f}s",
        f"configure {timing.configure_seconds:.3f}s",
        f"ninja {timing.ninja_seconds:.3f}s",
    ]
    values.extend(
        f"{phase.name} {phase.duration_seconds:.3f}s ({phase.steps} {'job' if phase.steps == 1 else 'jobs'})"
        for phase in timing.phases
    )
    if not timing.phases:
        values.append("no work")
    return "Build timing: " + " | ".join(values)


def build_root_for(state_root: Path, config: BuildConfig) -> Path:
    """Return the stable build root for a configuration."""
    key = f"{config.platform}-{config.architecture}-{config.compiler}-{config.build_type}"
    return state_root / "build" / key


def configure(project: ProjectSpec, root: Path, state_root: Path, config: BuildConfig) -> BuildResult:
    """Generate backend files without invoking the compiler."""
    toolchain = toolchain_resolve(config, state_root)
    build_root = build_root_for(state_root, config)
    build_root.mkdir(parents=True, exist_ok=True)
    result = BuildResult(generate(project, root, build_root, config, toolchain), toolchain)
    inputs = {str(path): path.stat().st_mtime_ns for path in project_provider_files(root)}
    state = {"drift_version": __version__, "inputs": inputs}
    (build_root / "configured.json").write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    return result


def build(
    project: ProjectSpec, root: Path, state_root: Path, config: BuildConfig, targets: tuple[str, ...] = ()
) -> BuildResult:
    """Configure and build selected targets with pinned Ninja."""
    started = time.perf_counter()
    configure_started = time.perf_counter()
    result = configure(project, root, state_root, config)
    configure_seconds = time.perf_counter() - configure_started
    ninja = ninja_resolve(state_root)
    arguments = [str(ninja), "-f", result.generated.ninja_file.name]
    arguments.extend(targets)
    log_path = result.generated.ninja_file.parent / ".ninja_log"
    log_snapshot = _ninja_log_snapshot(log_path)
    ninja_started = time.perf_counter()
    run(arguments, cwd=result.generated.ninja_file.parent, environment=result.toolchain.environment)
    ninja_seconds = time.perf_counter() - ninja_started
    entries = _ninja_log_entries(log_path, log_snapshot)
    output_phases = {_path_key(path): phase for path, phase in result.generated.output_phases.items()}
    timing = BuildTiming(
        time.perf_counter() - started,
        configure_seconds,
        ninja_seconds,
        _phase_timings(entries, output_phases),
    )
    return BuildResult(result.generated, result.toolchain, timing)


def clean(
    project: ProjectSpec, root: Path, state_root: Path, config: BuildConfig, targets: tuple[str, ...] = ()
) -> BuildResult:
    """Configure and remove outputs for selected targets with pinned Ninja."""
    result = configure(project, root, state_root, config)
    ninja = ninja_resolve(state_root)
    arguments = [str(ninja), "-f", result.generated.ninja_file.name, "-t", "clean", *targets]
    run(arguments, cwd=result.generated.ninja_file.parent, environment=result.toolchain.environment)
    return result
