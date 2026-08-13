from pathlib import Path

import pytest

from driftbuild.errors import ExecutionError
from driftbuild.model import BuildConfig, ProjectSpec
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
