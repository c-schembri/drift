"""Repeatable measurements of Drift configure and no-op build overhead."""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from driftbuild.build import build, configure
from driftbuild.errors import ExecutionError
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
        "platform": config.platform,
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


def performance_budget_check(payload: dict[str, object], budget_path: Path) -> None:
    """Fail when measured medians exceed explicit platform budgets."""
    def number(value: object) -> float:
        if not isinstance(value, (int, float, str)):
            raise TypeError("budget value is not numeric")
        return float(value)

    try:
        budgets = cast(dict[str, Any], json.loads(budget_path.read_text(encoding="utf-8")))
        platforms = cast(dict[str, Any], budgets["platforms"])
        platform_budget = cast(dict[str, object], platforms[str(payload["platform"])])
        configure_limit = number(platform_budget["configure_median_seconds"])
        no_op_limit = number(platform_budget["no_op_build_median_seconds"])
        configure_series = cast(dict[str, object], payload["configure"])
        no_op_series = cast(dict[str, object], payload["no_op_build"])
        configure = number(configure_series["median_seconds"])
        no_op = number(no_op_series["median_seconds"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ExecutionError(f"Invalid performance budget {budget_path}: {error}") from error
    failures: list[str] = []
    if configure > configure_limit:
        failures.append(f"configure {configure:.3f}s > {configure_limit:.3f}s")
    if no_op > no_op_limit:
        failures.append(f"no-op build {no_op:.3f}s > {no_op_limit:.3f}s")
    if failures:
        raise ExecutionError("Performance budget exceeded: " + ", ".join(failures))
