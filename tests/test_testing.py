from pathlib import Path

import pytest

from driftbuild.errors import ExecutionError
from driftbuild.model import BuildConfig, ProjectSpec, SuiteSpec, TargetRef, TargetSpec, TaskSpec
from driftbuild.model import TestSpec as DriftTestSpec
from driftbuild.process import ProcessResult
from driftbuild.testing import tests_run as run_tests


def test_failed_test_prints_captured_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = ProjectSpec("sample", tests=(DriftTestSpec("broken", ("test-command",)),))
    result = ProcessResult(("test-command",), 1, "stdout detail\n", "stderr detail\n")
    monkeypatch.setattr("driftbuild.testing.run", lambda *_args, **_kwargs: result)

    with pytest.raises(ExecutionError, match="Tests failed: broken"):
        run_tests(project, tmp_path, tmp_path / ".drift", BuildConfig("win32"))

    output = capsys.readouterr().out
    assert "--- broken output ---" in output
    assert "stdout detail" in output
    assert "stderr detail" in output


def test_isolated_test_expands_a_temporary_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectSpec(
        "sample",
        tests=(DriftTestSpec("isolated", ("test-command",), environment={"APP_HOME": "{temp}"}, isolated=True),),
    )
    observed: dict[str, object] = {}

    def run_fake(*_args, **kwargs):
        observed["environment"] = kwargs["environment"]
        return ProcessResult(("test-command",), 0, "", "")

    monkeypatch.setattr("driftbuild.testing.run", run_fake)

    run_tests(project, tmp_path, tmp_path / ".drift", BuildConfig("win32"))

    environment = observed["environment"]
    assert isinstance(environment, dict)
    assert environment["APP_HOME"] == environment["DRIFT_TEST_TEMP"]
    assert environment["APPDATA"] == environment["DRIFT_TEST_TEMP"]


def test_target_bound_test_runs_the_built_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "sample.exe"
    working_directory = tmp_path / "runtime"
    target = TargetSpec(
        "sample",
        "executable",
        outputs=(executable,),
        run_environment={"TARGET_ENV": "set"},
        run_working_directory=working_directory,
    )
    project = ProjectSpec("sample", targets=(target,), tests=(DriftTestSpec("sample", target=TargetRef("sample")),))
    observed: list[tuple[tuple[str, ...], dict[str, object]]] = []

    class Generated:
        outputs = {"sample": (executable,)}
        ninja_file = tmp_path / "build.ninja"

    class Result:
        generated = Generated()

    monkeypatch.setattr("driftbuild.testing.build", lambda *_args, **_kwargs: Result())

    def run_fake(command, **_kwargs):
        observed.append((tuple(command), _kwargs))
        return ProcessResult(tuple(command), 0, "", "")

    monkeypatch.setattr("driftbuild.testing.run", run_fake)

    run_tests(project, tmp_path, tmp_path / ".drift", BuildConfig("win32"))

    assert observed[0][0] == (str(executable),)
    assert observed[0][1]["cwd"] == working_directory
    assert observed[0][1]["environment"]["TARGET_ENV"] == "set"


def test_suite_runs_its_dependency_graph(tmp_path: Path) -> None:
    events: list[str] = []
    suite = SuiteSpec(
        "full",
        (
            TaskSpec("first", handler=lambda _context: events.append("first")),
            TaskSpec("second", handler=lambda _context: events.append("second"), dependencies=("first",)),
        ),
    )

    results = run_tests(ProjectSpec("sample", suites=(suite,)), tmp_path, tmp_path / ".drift", BuildConfig("win32"))

    assert events == ["first", "second"]
    assert results[0].name == "full"


def test_suite_task_can_reference_a_declared_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[tuple[str, ...]] = []
    suite = SuiteSpec("full", (TaskSpec("unit", test="unit"),))
    project = ProjectSpec(
        "sample",
        tests=(DriftTestSpec("unit", ("unit-command",)),),
        suites=(suite,),
    )

    def run_fake(command, **_kwargs):  # type: ignore[no-untyped-def]
        observed.append(tuple(command))
        return ProcessResult(tuple(command), 0, "", "")

    monkeypatch.setattr("driftbuild.testing.run", run_fake)

    results = run_tests(project, tmp_path, tmp_path / ".drift", BuildConfig("win32"), names=("full",))

    assert observed == [("unit-command",)]
    assert results[0].name == "full"
