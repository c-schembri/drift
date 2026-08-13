"""Repeatable measurements of Drift configure and no-op build overhead."""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from driftbuild.build import build, configure
from driftbuild.model import BuildConfig, ProjectSpec


@dataclass(frozen=True)
class PerformanceSeries:
    """Timing samples and stable summary statistics for one operation."""

    samples: tuple[float, ...]

    def payload(self) -> dict[str, object]:
        """Return a JSON-compatible timing summary."""
        return {
            "samples_seconds": self.samples,
            "minimum_seconds": min(self.samples),
            "median_seconds": statistics.median(self.samples),
            "maximum_seconds": max(self.samples),
        }


def _measure(operation: Callable[[], object], repetitions: int) -> PerformanceSeries:
    samples: list[float] = []
    for _index in range(repetitions):
        started = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - started)
    return PerformanceSeries(tuple(samples))


def performance_run(
    project: ProjectSpec,
    root: Path,
    state_root: Path,
    config: BuildConfig,
    repetitions: int = 5,
    output: Path | None = None,
) -> dict[str, object]:
    """Build once, then measure warm configuration and no-op build latency."""
    if repetitions < 1:
        raise ValueError("Performance repetitions must be positive")
    initial = build(project, root, state_root, config)
    assert initial.timing is not None
    configure_series = _measure(lambda: configure(project, root, state_root, config), repetitions)
    no_op_series = _measure(lambda: build(project, root, state_root, config), repetitions)
    payload: dict[str, object] = {
        "schema": 1,
        "project": project.name,
        "configuration": config.build_type,
        "repetitions": repetitions,
        "initial_build_seconds": initial.timing.total_seconds,
        "configure": configure_series.payload(),
        "no_op_build": no_op_series.payload(),
    }
    report = output or state_root / "performance.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload["report"] = str(report)
    return payload
