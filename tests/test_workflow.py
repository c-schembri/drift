import sys
import threading
import time
from pathlib import Path

import pytest

from driftbuild.errors import ExecutionError
from driftbuild.model import ProjectSpec, TaskSpec
from driftbuild.workflow import tasks_run


def test_dependencies_and_resource_locks_are_respected(tmp_path: Path) -> None:
    events: list[str] = []
    event_lock = threading.Lock()

    def record(name: str):
        def handler(_context):
            with event_lock:
                events.append(f"start:{name}")
            time.sleep(0.01)
            with event_lock:
                events.append(f"end:{name}")

        return handler

    project = ProjectSpec(
        "workflow",
        tasks=(
            TaskSpec("prepare", handler=record("prepare")),
            TaskSpec("one", handler=record("one"), dependencies=("prepare",), resources=("device",)),
            TaskSpec("two", handler=record("two"), dependencies=("prepare",), resources=("device",)),
        ),
    )

    results = tasks_run(project, ("one", "two"), tmp_path, tmp_path / ".drift", jobs=3)

    assert events.index("end:prepare") < events.index("start:one")
    assert events.index("end:one") < events.index("start:two") or events.index("end:two") < events.index("start:one")
    assert {result.name for result in results} == {"prepare", "one", "two"}


def test_failure_skips_dependents_but_runs_independent_tasks(tmp_path: Path) -> None:
    events: list[str] = []

    def fail(_context):
        raise RuntimeError("broken")

    project = ProjectSpec(
        "workflow",
        tasks=(
            TaskSpec("failed", handler=fail),
            TaskSpec("skipped", handler=lambda _context: events.append("skipped"), dependencies=("failed",)),
            TaskSpec("independent", handler=lambda _context: events.append("independent")),
        ),
    )

    with pytest.raises(ExecutionError, match="failed: broken.*skipped: dependency failed"):
        tasks_run(project, (), tmp_path, tmp_path / ".drift", jobs=2)

    assert events == ["independent"]


def test_command_tasks_report_nonzero_exit(tmp_path: Path) -> None:
    project = ProjectSpec(
        "workflow",
        tasks=(TaskSpec("failed", command=(sys.executable, "-c", "raise SystemExit(7)")),),
    )

    with pytest.raises(ExecutionError, match=r"failed: Command failed \(7\)"):
        tasks_run(project, (), tmp_path, tmp_path / ".drift")
