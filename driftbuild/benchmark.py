"""Simple repeatable benchmark runner and JSON report writer."""

from __future__ import annotations

import json
import os
import statistics
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from driftbuild.build import build
from driftbuild.errors import ExecutionError
from driftbuild.model import BuildConfig, ProjectSpec
from driftbuild.process import run


@dataclass(frozen=True)
class BenchmarkResult:
    """Timing samples and summary for one benchmark."""

    name: str
    samples_seconds: tuple[float, ...]
    median_seconds: float
    minimum_seconds: float


def benchmarks_run(
    project: ProjectSpec, root: Path, state_root: Path, config: BuildConfig, names: Sequence[str] = ()
) -> tuple[BenchmarkResult, ...]:
    """Build prerequisites, run warmups and measured repetitions, and persist results."""
    selected = [item for item in project.benchmarks if not names or item.name in names]
    unknown = set(names) - {item.name for item in selected}
    if unknown:
        raise ExecutionError(f"Unknown benchmarks: {', '.join(sorted(unknown))}")
    prerequisites = tuple(sorted({reference.name for item in selected for reference in item.build_targets}))
    if prerequisites:
        build(project, root, state_root, config, prerequisites)
    results: list[BenchmarkResult] = []
    for item in selected:
        if item.warmups < 0 or item.repetitions < 1:
            raise ExecutionError(f"Benchmark {item.name} has invalid repetition policy")
        environment = dict(os.environ)
        environment.update(item.environment)
        for _ in range(item.warmups):
            run(item.command, cwd=root, environment=environment, timeout_seconds=item.timeout_seconds)
        samples: list[float] = []
        for _ in range(item.repetitions):
            started = time.perf_counter()
            run(item.command, cwd=root, environment=environment, timeout_seconds=item.timeout_seconds)
            samples.append(time.perf_counter() - started)
        results.append(BenchmarkResult(item.name, tuple(samples), statistics.median(samples), min(samples)))
    report = state_root / "benchmarks.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps([asdict(result) for result in results], indent=2) + "\n", encoding="utf-8")
    return tuple(results)
