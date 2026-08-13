import sys
import threading
import time
from pathlib import Path

import pytest

from driftbuild.errors import ExecutionError
from driftbuild.model import BuildConfig, CommandSpec, MatrixSpec, ProjectSpec, TargetSpec, TaskSpec
from driftbuild.model import TestSpec as DriftTestSpec
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


def test_direct_test_and_target_tasks_do_not_launch_nested_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, object]] = []
    project = ProjectSpec(
        "workflow",
        targets=(TargetSpec("app", "executable"),),
        tests=(DriftTestSpec("smoke", ("smoke-command",)),),
        tasks=(
            TaskSpec("build", targets=("app",)),
            TaskSpec("test", test="smoke", dependencies=("build",)),
        ),
    )
    monkeypatch.setattr(
        "driftbuild.build.build",
        lambda _project, _root, _state, config, targets, **_kwargs: events.append(("build", (config, targets))),
    )
    monkeypatch.setattr(
        "driftbuild.testing.tests_run",
        lambda _project, _root, _state, config, names, **_kwargs: events.append(("test", (config, names))),
    )

    tasks_run(
        project,
        ("test",),
        tmp_path,
        tmp_path / ".drift",
        config=BuildConfig("win32"),
    )

    assert [event[0] for event in events] == ["build", "test"]
    assert events[0][1][1] == ("app",)  # type: ignore[index]
    assert events[1][1][1] == ("smoke",)  # type: ignore[index]


def test_direct_matrix_task_uses_declared_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected: list[str] = []
    matrix = MatrixSpec("client", (("build-type", ("debug", "release")),), targets=("app",))
    project = ProjectSpec(
        "workflow",
        targets=(TargetSpec("app", "executable"),),
        tasks=(TaskSpec("matrix", matrix="client"),),
        matrices=(matrix,),
    )
    monkeypatch.setattr(
        "driftbuild.matrix.matrix_run",
        lambda value, *_args, **_kwargs: selected.append(value.name),
    )

    tasks_run(project, (), tmp_path, tmp_path / ".drift", config=BuildConfig("win32"))

    assert selected == ["client"]


def test_provider_command_task_invokes_the_declared_handler_in_process(tmp_path: Path) -> None:
    received: list[tuple[str, ...]] = []

    def handler(_context, arguments):  # type: ignore[no-untyped-def]
        received.append(arguments)

    project = ProjectSpec(
        "workflow",
        commands=(CommandSpec(("quality",), "Run quality", handler, passthrough=True),),
        tasks=(TaskSpec("quality", provider_command=("quality", "--quick")),),
    )

    tasks_run(project, (), tmp_path, tmp_path / ".drift", config=BuildConfig("win32"))

    assert received == [("--quick",)]
