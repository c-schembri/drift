"""Public build service backed by generated Ninja files."""

from __future__ import annotations

import json
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
class BuildResult:
    """Resolved build paths and backend state."""

    generated: GeneratedBuild
    toolchain: Toolchain


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
    result = configure(project, root, state_root, config)
    ninja = ninja_resolve(state_root)
    arguments = [str(ninja), "-f", result.generated.ninja_file.name]
    arguments.extend(targets)
    run(arguments, cwd=result.generated.ninja_file.parent, environment=result.toolchain.environment)
    return result
