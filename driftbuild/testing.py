"""Test selection and execution."""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from driftbuild.build import build
from driftbuild.errors import ExecutionError
from driftbuild.model import BuildConfig, ProjectSpec, TestSpec
from driftbuild.process import run


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
    selected = [
        test
        for test in project.tests
        if (not names or test.name in names) and (not labels or set(labels) <= set(test.labels))
    ]
    unknown = set(names) - {test.name for test in selected}
    if unknown:
        raise ExecutionError(f"Unknown tests: {', '.join(sorted(unknown))}")
    prerequisites = tuple(sorted({reference.name for test in selected for reference in test.build_targets}))
    if prerequisites:
        build(project, root, state_root, config, prerequisites)

    def execute(test: TestSpec) -> TestResult:
        started = time.perf_counter()
        environment = dict(os.environ)
        environment.update(test.environment)
        result = run(
            test.command,
            cwd=root / test.working_directory if test.working_directory else root,
            environment=environment,
            timeout_seconds=test.timeout_seconds,
            capture=True,
            check=False,
        )
        output = result.stdout + result.stderr
        return TestResult(test.name, result.returncode == 0, time.perf_counter() - started, output)

    with ThreadPoolExecutor(max_workers=jobs or max(1, min(32, os.cpu_count() or 1))) as executor:
        results = tuple(executor.map(execute, selected))
    failures = [result.name for result in results if not result.passed]
    if failures:
        raise ExecutionError(f"Tests failed: {', '.join(failures)}")
    return results
