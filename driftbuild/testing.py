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
            result = run(
                test.command,
                cwd=root / test.working_directory if test.working_directory else root,
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
        results = tuple(executor.map(execute, selected))
    failures = [result for result in results if not result.passed]
    if failures:
        for result in failures:
            print(f"--- {result.name} output ---")
            print(result.output, end="" if result.output.endswith("\n") else "\n")
        raise ExecutionError(f"Tests failed: {', '.join(result.name for result in failures)}")
    return results
