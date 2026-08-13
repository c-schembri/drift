"""Test selection and execution."""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from driftbuild.build import build
from driftbuild.errors import ExecutionError
from driftbuild.locking import cache_lock
from driftbuild.model import BuildConfig, ProjectSpec, TestSpec
from driftbuild.process import run
from driftbuild.runtime import provider_command
from driftbuild.workflow import tasks_run


@dataclass(frozen=True)
class TestResult:
    """One completed test invocation."""

    name: str
    passed: bool
    duration_seconds: float
    output: str


def tests_run(
    project: ProjectSpec,
    root: Path,
    state_root: Path,
    config: BuildConfig,
    names: Sequence[str] = (),
    labels: Sequence[str] = (),
    jobs: int | None = None,
) -> tuple[TestResult, ...]:
    """Build prerequisites, then execute selected tests in parallel."""
    available = {test.name for test in project.tests} | {suite.name for suite in project.suites}
    unknown = set(names) - available
    if unknown:
        raise ExecutionError(f"Unknown tests or suites: {', '.join(sorted(unknown))}")
    selected = [
        test
        for test in project.tests
        if (not names or test.name in names) and (not labels or set(labels) <= set(test.labels))
    ]
    selected_suites = [
        suite
        for suite in project.suites
        if (not names or suite.name in names) and (not labels or set(labels) <= set(suite.labels))
    ]
    prerequisites = tuple(
        sorted(
            {
                reference.name
                for test in selected
                for reference in (*test.build_targets, *((test.target,) if test.target is not None else ()))
            }
        )
    )
    build_result = None
    if prerequisites:
        build_result = build(project, root, state_root, config, prerequisites)

    def expand(value: str, test: TestSpec) -> str:
        if value == "{root}":
            return str(root)
        if value == "{build}":
            if build_result is None:
                return str(state_root)
            return str(build_result.generated.ninja_file.parent)
        if value == "{out}" or value.startswith("{out:"):
            if test.target is None or build_result is None:
                raise ExecutionError(f"Test {test.name} output placeholder requires a target")
            outputs = build_result.generated.outputs[test.target.name]
            index = 0 if value == "{out}" else int(value[5:-1])
            try:
                return str(outputs[index].resolve())
            except IndexError as error:
                raise ExecutionError(f"Test {test.name} has invalid output placeholder: {value}") from error
        return value

    def execute(test: TestSpec) -> TestResult:
        started = time.perf_counter()
        target = (
            next(item for item in project.targets if item.name == test.target.name)
            if test.target is not None
            else None
        )
        environment = dict(os.environ)
        if target is not None:
            environment.update(target.run_environment)
        environment.update(test.environment)
        temporary = tempfile.TemporaryDirectory(prefix=f"drift-{test.name}-") if test.isolated else None
        try:
            if temporary is not None:
                home = Path(temporary.name)
                environment.update(
                    {
                        "HOME": str(home),
                        "USERPROFILE": str(home),
                        "APPDATA": str(home),
                        "XDG_CONFIG_HOME": str(home),
                        "TEMP": str(home),
                        "TMP": str(home),
                        "DRIFT_TEST_TEMP": str(home),
                    }
                )
                environment = {name: value.replace("{temp}", str(home)) for name, value in environment.items()}
            if test.handler is not None:
                command = (*provider_command(root, test.handler), *(expand(value, test) for value in test.arguments))
            elif test.command:
                command = (*test.command, *(expand(value, test) for value in test.arguments))
            else:
                assert test.target is not None and build_result is not None
                assert target is not None
                template = target.run_command or ("{out}",)
                command = (*(expand(value, test) for value in template), *(expand(value, test) for value in test.arguments))
            working_directory = test.working_directory
            if working_directory is None and target is not None:
                working_directory = target.run_working_directory
            result = run(
                command,
                cwd=root / working_directory if working_directory else root,
                environment=environment,
                timeout_seconds=test.timeout_seconds,
                capture=True,
                check=False,
            )
        finally:
            if temporary is not None:
                temporary.cleanup()
        output = result.stdout + result.stderr
        return TestResult(test.name, result.returncode == 0, time.perf_counter() - started, output)

    with ThreadPoolExecutor(max_workers=jobs or max(1, min(32, os.cpu_count() or 1))) as executor:
        results = list(executor.map(execute, selected))
    for suite in selected_suites:
        started = time.perf_counter()
        if suite.exclusive:
            with cache_lock(state_root / "locks" / f"suite-{suite.name}.lock"):
                tasks_run(ProjectSpec(suite.name, tasks=suite.tasks), (), root, state_root, jobs)
        else:
            tasks_run(ProjectSpec(suite.name, tasks=suite.tasks), (), root, state_root, jobs)
        results.append(TestResult(suite.name, True, time.perf_counter() - started, ""))
    result_tuple = tuple(results)
    failures = [result for result in result_tuple if not result.passed]
    if failures:
        for result in failures:
            print(f"--- {result.name} output ---")
            print(result.output, end="" if result.output.endswith("\n") else "\n")
        raise ExecutionError(f"Tests failed: {', '.join(result.name for result in failures)}")
    return result_tuple
