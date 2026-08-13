import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from driftbuild.build import BuildTiming
from driftbuild.errors import ExecutionError
from driftbuild.model import BuildConfig, ProjectSpec
from driftbuild.performance import performance_budget_check, performance_run


def test_performance_report_measures_configure_and_no_op_build(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    timing = BuildTiming(0.25, 0.1, 0.15, ())
    monkeypatch.setattr("driftbuild.performance.configure", lambda *_arguments: None)
    monkeypatch.setattr(
        "driftbuild.performance.build",
        lambda *_arguments: SimpleNamespace(timing=timing),
    )

    payload = performance_run(ProjectSpec("sample"), tmp_path, tmp_path / ".drift", BuildConfig("linux"), 2)

    assert payload["repetitions"] == 2
    assert len(payload["configure"]["samples_seconds"]) == 2  # type: ignore[index]
    assert len(payload["no_op_build"]["samples_seconds"]) == 2  # type: ignore[index]
    report = tmp_path / ".drift/performance.json"
    assert json.loads(report.read_text(encoding="utf-8"))["schema"] == 1


def test_performance_budget_reports_exceeded_medians(tmp_path: Path) -> None:
    budget = tmp_path / "budget.json"
    budget.write_text(
        '{"platforms":{"linux":{"configure_median_seconds":0.1,"no_op_build_median_seconds":0.2}}}',
        encoding="utf-8",
    )
    payload: dict[str, object] = {
        "platform": "linux",
        "configure": {"median_seconds": 0.15},
        "no_op_build": {"median_seconds": 0.1},
    }

    with pytest.raises(ExecutionError, match=r"configure 0.150s > 0.100s"):
        performance_budget_check(payload, budget)
