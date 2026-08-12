"""Build and launch executable targets."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from driftbuild.build import build, build_timing_render
from driftbuild.errors import ExecutionError
from driftbuild.graph import project_validate, transitive_targets
from driftbuild.model import BuildConfig, ProjectSpec, TargetSpec
from driftbuild.process import run


def executable_select(project: ProjectSpec, requested: str | None = None) -> TargetSpec:
    """Select an explicit executable or the sole executable reachable from project defaults."""
    targets = project_validate(project)
    if requested is not None:
        target = targets.get(requested)
        if target is None:
            raise ExecutionError(f"Unknown target: {requested}")
        if target.kind != "executable":
            raise ExecutionError(f"Target is not executable: {requested}")
        return target

    default_names = transitive_targets(targets, (reference.name for reference in project.defaults))
    candidates = [target for target in project.targets if target.kind == "executable" and target.name in default_names]
    if not candidates:
        candidates = [target for target in project.targets if target.kind == "executable"]
    if not candidates:
        raise ExecutionError("Project has no executable targets")
    if len(candidates) > 1:
        names = ", ".join(target.name for target in candidates)
        raise ExecutionError(f"Project has multiple executable targets; specify one: {names}")
    return candidates[0]


def build_and_run(
    project: ProjectSpec,
    root: Path,
    state_root: Path,
    config: BuildConfig,
    target_name: str | None = None,
    arguments: Sequence[str] = (),
) -> int:
    """Build one selected executable and run it from the project root."""
    target = executable_select(project, target_name)
    result = build(project, root, state_root, config, (target.name,))
    assert result.timing is not None
    print(build_timing_render(result.timing), flush=True)
    outputs = result.generated.outputs[target.name]
    if not outputs:
        raise ExecutionError(f"Executable target has no output: {target.name}")
    executable = outputs[0].resolve()
    if not executable.is_file():
        raise ExecutionError(f"Built executable does not exist: {executable}")

    environment = dict(os.environ)
    targets = project_validate(project)
    reachable = transitive_targets(targets, (target.name,))
    runtime_directories = [executable.parent]
    for name in sorted(reachable):
        dependency = targets[name]
        if dependency.kind != "external_library":
            continue
        for output in result.generated.outputs[name]:
            filename = output.name.casefold()
            is_runtime = output.suffix.casefold() in (".dll", ".so", ".dylib") or ".so." in filename
            if is_runtime and output.parent not in runtime_directories:
                runtime_directories.append(output.parent)
    runtime_path = os.pathsep.join(str(path) for path in runtime_directories)
    environment["PATH"] = os.pathsep.join((runtime_path, environment.get("PATH", "")))
    if sys.platform == "darwin":
        environment["DYLD_LIBRARY_PATH"] = os.pathsep.join(
            (runtime_path, environment.get("DYLD_LIBRARY_PATH", ""))
        )
    elif sys.platform != "win32":
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(
            (runtime_path, environment.get("LD_LIBRARY_PATH", ""))
        )
    completed = run((str(executable), *arguments), cwd=root, environment=environment, check=False)
    return completed.returncode
