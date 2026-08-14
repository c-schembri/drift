"""Build and launch executable targets."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from driftbuild.errors import ExecutionError
from driftbuild.graph import project_validate, transitive_targets
from driftbuild.model import BuildConfig, ProjectSpec, TargetSpec
from driftbuild.process import run


@dataclass(frozen=True)
class LaunchSpec:
    """Resolved process data for one runnable target."""

    target: str
    command: tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]
    runtime_directories: tuple[Path, ...] = ()
    executable: Path | None = None


def executable_select(project: ProjectSpec, requested: str | None = None) -> TargetSpec:
    """Select an explicit executable or the sole executable reachable from project defaults."""
    targets = project_validate(project)
    if requested is not None:
        target = targets.get(requested)
        if target is None:
            raise ExecutionError(f"Unknown target: {requested}")
        if target.kind != "executable" and not target.run_command:
            raise ExecutionError(f"Target is not executable: {requested}")
        return target

    default_names = transitive_targets(targets, (reference.name for reference in project.defaults))
    candidates = [
        target
        for target in project.targets
        if (target.kind == "executable" or target.run_command) and target.name in default_names
    ]
    if not candidates:
        candidates = [target for target in project.targets if target.kind == "executable" or target.run_command]
    if not candidates:
        raise ExecutionError("Project has no executable targets")
    if len(candidates) > 1:
        names = ", ".join(target.name for target in candidates)
        raise ExecutionError(f"Project has multiple executable targets; specify one: {names}")
    return candidates[0]


def launch_spec(
    project: ProjectSpec,
    outputs: Mapping[str, tuple[Path, ...]],
    root: Path,
    build_root: Path,
    target_name: str | None = None,
) -> LaunchSpec:
    """Resolve one runnable target into stable process data without launching it."""
    target = executable_select(project, target_name)
    target_outputs = outputs[target.name]
    if target.run_command:

        def expand(value: str) -> str:
            if value == "{root}":
                return str(root)
            if value == "{build}":
                return str(build_root)
            if value == "{out}":
                if not target_outputs:
                    raise ExecutionError(f"Runnable target {target.name} has no output")
                return str(target_outputs[0].resolve())
            if value.startswith("{out:") and value.endswith("}"):
                try:
                    return str(target_outputs[int(value[5:-1])].resolve())
                except (IndexError, ValueError) as error:
                    raise ExecutionError(
                        f"Runnable target {target.name} has invalid output placeholder {value}"
                    ) from error
            return value

        working_directory = root / target.run_working_directory if target.run_working_directory else root
        return LaunchSpec(
            target.name,
            tuple(expand(value) for value in target.run_command),
            working_directory,
            target.run_environment,
        )
    if not target_outputs:
        raise ExecutionError(f"Executable target has no output: {target.name}")
    executable = target_outputs[0].resolve()
    targets = project_validate(project)
    reachable = transitive_targets(targets, (target.name,))
    runtime_directories = [executable.parent]
    for name in sorted(reachable):
        dependency = targets[name]
        if dependency.kind != "external_library":
            continue
        for output in outputs[name]:
            filename = output.name.casefold()
            is_runtime = output.suffix.casefold() in (".dll", ".so", ".dylib") or ".so." in filename
            if is_runtime and output.parent not in runtime_directories:
                runtime_directories.append(output.parent)
    return LaunchSpec(
        target.name,
        (str(executable),),
        executable.parent,
        target.run_environment,
        tuple(runtime_directories),
        executable,
    )


def launch(spec: LaunchSpec, arguments: Sequence[str] = ()) -> int:
    """Launch one resolved target with its declared environment and runtime paths."""
    if spec.executable is not None and not spec.executable.is_file():
        raise ExecutionError(f"Built executable does not exist: {spec.executable}")
    environment = dict(os.environ)
    environment.update(spec.environment)
    if spec.runtime_directories:
        runtime_path = os.pathsep.join(str(path) for path in spec.runtime_directories)
        environment["PATH"] = os.pathsep.join((runtime_path, environment.get("PATH", "")))
        if sys.platform == "darwin":
            environment["DYLD_LIBRARY_PATH"] = os.pathsep.join(
                (runtime_path, environment.get("DYLD_LIBRARY_PATH", ""))
            )
        elif sys.platform != "win32":
            environment["LD_LIBRARY_PATH"] = os.pathsep.join(
                (runtime_path, environment.get("LD_LIBRARY_PATH", ""))
            )
    completed = run((*spec.command, *arguments), cwd=spec.working_directory, environment=environment, check=False)
    return completed.returncode


def build_and_run(
    project: ProjectSpec,
    root: Path,
    state_root: Path,
    config: BuildConfig,
    target_name: str | None = None,
    arguments: Sequence[str] = (),
    build_targets: Sequence[str] = (),
) -> int:
    """Build one selected executable and run it from its output directory."""
    from driftbuild.build import build, build_timing_render

    target = executable_select(project, target_name)
    selected_build_targets = tuple(dict.fromkeys((*build_targets, target.name)))
    result = build(project, root, state_root, config, selected_build_targets)
    assert result.timing is not None
    print(build_timing_render(result.timing), flush=True)
    spec = launch_spec(project, result.generated.outputs, root, result.generated.ninja_file.parent, target.name)
    return launch(spec, arguments)
