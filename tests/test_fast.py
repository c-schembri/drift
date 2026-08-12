import pytest

from driftbuild.fast import _operation_find, main


def test_operation_find_ignores_global_option_values() -> None:
    assert _operation_find(["--root", "build", "--compiler=msvc", "run"]) == ("run", 3)


def test_operation_find_does_not_treat_program_argument_as_build() -> None:
    assert _operation_find(["run", "--", "build"]) == ("run", 0)


def test_no_op_fast_path_reports_timing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("driftbuild.fast._no_op", lambda _arguments: True)

    assert main(["build"]) == 0
    output = capsys.readouterr().out
    assert "Build timing: total " in output
    assert "| no work" in output
