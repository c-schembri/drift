"""Build and workflow graph validation."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.model import (
    Artifact,
    Dependency,
    Deployment,
    PackageTargetRef,
    ProjectSpec,
    TargetDependency,
    TargetSpec,
)


def target_map(project: ProjectSpec) -> dict[str, TargetSpec]:
    """Return targets by name after validating uniqueness."""
    targets: dict[str, TargetSpec] = {}
    for target in project.targets:
        if target.name in targets:
            raise ConfigurationError(f"Duplicate target name: {target.name}")
        targets[target.name] = target
    return targets


def _target_edges(target: TargetSpec) -> set[str]:
    edges = {
        item.target.name
        for item in target.dependencies
        if isinstance(item, TargetDependency) and not isinstance(item.target, PackageTargetRef)
    }
    edges.update(item.name for item in target.objects)
    for value in (*target.sources, *target.runtime_files):
        if isinstance(value, Deployment):
            value = value.source
        if isinstance(value, Artifact):
            edges.add(value.target.name)
    for dependency in target.dependencies:
        if not isinstance(dependency, Dependency):
            continue
        for value in dependency.runtime_files:
            if isinstance(value, Deployment):
                value = value.source
            if isinstance(value, Artifact):
                edges.add(value.target.name)
    if target.action is not None:
        for value in (*target.action.inputs, *target.action.implicit_inputs, *target.action.order_only):
            if isinstance(value, Artifact):
                edges.add(value.target.name)
    return edges


def _cycles_validate(graph: dict[str, set[str]]) -> None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> None:
        current = state.get(name, 0)
        if current == 2:
            return
        if current == 1:
            start = stack.index(name)
            raise ConfigurationError(f"Target cycle: {' -> '.join((*stack[start:], name))}")
        state[name] = 1
        stack.append(name)
        for dependency in sorted(graph[name]):
            visit(dependency)
        stack.pop()
        state[name] = 2

    for name in sorted(graph):
        visit(name)


def project_validate(project: ProjectSpec) -> dict[str, TargetSpec]:
    """Validate all project-level identities, references, outputs, and cycles."""
    if not project.name:
        raise ConfigurationError("Project name cannot be empty")
    targets = target_map(project)
    packages = {package.name for package in project.packages}
    if len(packages) != len(project.packages):
        raise ConfigurationError("Package names must be unique")
    if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None for name in packages):
        raise ConfigurationError("Package names contain invalid characters")
    if len({name.casefold() for name in packages}) != len(packages):
        raise ConfigurationError("Package names must not collide by case")
    outputs: dict[str, str] = {}
    graph: dict[str, set[str]] = {}

    def deployment_validate(value: object) -> None:
        if not isinstance(value, Deployment):
            return
        destination = value.destination
        if destination.is_absolute() or ".." in destination.parts or destination in (Path(""), Path(".")):
            raise ConfigurationError(f"Runtime destination must be a relative file path: {destination}")

    for target in project.targets:
        for value in target.runtime_files:
            deployment_validate(value)
        for dependency in target.dependencies:
            if isinstance(dependency, Dependency):
                for value in dependency.runtime_files:
                    deployment_validate(value)
        if target.kind in ("custom", "external_library") and target.action is None:
            raise ConfigurationError(f"Target {target.name} requires an action")
        if target.action is not None and tuple(target.outputs) != tuple(target.action.outputs):
            raise ConfigurationError(f"Target {target.name} outputs do not match its action")
        unknown_packages = sorted(
            dependency.target.package
            for dependency in target.dependencies
            if isinstance(dependency, TargetDependency)
            and isinstance(dependency.target, PackageTargetRef)
            and dependency.target.package not in packages
        )
        if unknown_packages:
            raise ConfigurationError(f"Target {target.name} references unknown packages: {', '.join(unknown_packages)}")
        for output in target.outputs:
            key = output.as_posix().casefold()
            owner = outputs.get(key)
            if owner is not None:
                raise ConfigurationError(f"Output {output.as_posix()} is produced by {owner} and {target.name}")
            outputs[key] = target.name
        edges = _target_edges(target)
        unknown = sorted(edge for edge in edges if edge not in targets)
        if unknown:
            raise ConfigurationError(f"Target {target.name} references unknown targets: {', '.join(unknown)}")
        graph[target.name] = edges

    for default in project.defaults:
        if default.name not in targets:
            raise ConfigurationError(f"Unknown default target: {default.name}")

    task_names = {task.name for task in project.tasks}
    if len(task_names) != len(project.tasks):
        raise ConfigurationError("Task names must be unique")
    for task in project.tasks:
        if task.command is None and task.handler is None and not task.dependencies:
            raise ConfigurationError(f"Task {task.name} requires a command, handler, or dependency")
        if task.command is not None and task.handler is not None:
            raise ConfigurationError(f"Task {task.name} cannot declare both command and handler")
        unknown = sorted(set(task.dependencies) - task_names)
        if unknown:
            raise ConfigurationError(f"Task {task.name} references unknown tasks: {', '.join(unknown)}")
        if task.retries < 0:
            raise ConfigurationError(f"Task {task.name} retries cannot be negative")

    pool_names = {pool.name for pool in project.pools}
    if len(pool_names) != len(project.pools):
        raise ConfigurationError("Pool names must be unique")
    for pool in project.pools:
        if pool.depth < 1:
            raise ConfigurationError(f"Pool {pool.name} depth must be positive")
    for target in project.targets:
        if target.action is not None and target.action.pool not in (None, "console"):
            if target.action.pool not in pool_names:
                raise ConfigurationError(f"Target {target.name} references unknown pool: {target.action.pool}")

    for test in project.tests:
        unknown = sorted(reference.name for reference in test.build_targets if reference.name not in targets)
        if unknown:
            raise ConfigurationError(f"Test {test.name} references unknown targets: {', '.join(unknown)}")
    test_names = {test.name for test in project.tests}
    for matrix in project.matrices:
        available = targets if matrix.operation == "build" else test_names
        unknown = sorted(set(matrix.targets) - set(available))
        if unknown:
            raise ConfigurationError(f"Matrix {matrix.name} references unknown {matrix.operation} targets: {', '.join(unknown)}")
    for benchmark in project.benchmarks:
        unknown = sorted(reference.name for reference in benchmark.build_targets if reference.name not in targets)
        if unknown:
            raise ConfigurationError(f"Benchmark {benchmark.name} references unknown targets: {', '.join(unknown)}")
    artifact_names = {artifact.name for artifact in project.artifacts}
    if len(artifact_names) != len(project.artifacts):
        raise ConfigurationError("Artifact names must be unique")
    for artifact in project.artifacts:
        for value in artifact.files:
            if isinstance(value, Artifact) and value.target.name not in targets:
                raise ConfigurationError(f"Artifact {artifact.name} references unknown target: {value.target.name}")
    for release in project.releases:
        unknown = sorted(set(release.artifacts) - artifact_names)
        if unknown:
            raise ConfigurationError(f"Release {release.name} references unknown artifacts: {', '.join(unknown)}")

    _cycles_validate(graph)
    _cycles_validate({task.name: set(task.dependencies) for task in project.tasks})
    return targets


def transitive_targets(targets: dict[str, TargetSpec], roots: Iterable[str]) -> set[str]:
    """Return target names reachable from roots, including roots."""
    result: set[str] = set()

    def add(name: str) -> None:
        if name in result:
            return
        result.add(name)
        for dependency in _target_edges(targets[name]):
            add(dependency)

    for root in roots:
        if root not in targets:
            raise ConfigurationError(f"Unknown target: {root}")
        add(root)
    return result
