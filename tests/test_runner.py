from pathlib import Path
from types import SimpleNamespace

import pytest

from driftbuild.errors import ExecutionError
from driftbuild.model import ActionSpec, BuildConfig, ProjectSpec, TargetRef, TargetSpec
from driftbuild.runner import build_and_run, executable_select


def test_selects_executable_reachable_from_defaults() -> None:
    project = ProjectSpec(
        "sample",
        targets=(
            TargetSpec("tool", "executable"),
            TargetSpec("app", "executable"),
            TargetSpec("all", "alias", objects=(TargetRef("app"),)),
        ),
        defaults=(TargetRef("all"),),
    )

    assert executable_select(project).name == "app"


def test_requires_target_when_multiple_executables_are_equally_selectable() -> None:
    project = ProjectSpec(
        "sample",
        targets=(TargetSpec("one", "executable"), TargetSpec("two", "executable")),
    )

    with pytest.raises(ExecutionError, match="multiple executable targets; specify one: one, two"):
        executable_select(project)


def test_explicit_target_must_be_executable() -> None:
    project = ProjectSpec("sample", targets=(TargetSpec("library", "static_library", outputs=(Path("x"),)),))

    with pytest.raises(ExecutionError, match="not executable"):
        executable_select(project, "library")


def test_runs_executable_from_its_output_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "build" / "bin" / "sample.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    project = ProjectSpec(
        "sample",
        targets=(TargetSpec("sample", "executable"),),
        defaults=(TargetRef("sample"),),
    )
    result = SimpleNamespace(
        timing=object(),
        generated=SimpleNamespace(outputs={"sample": (executable,)}, ninja_file=tmp_path / "build/build.ninja"),
    )
    observed: dict[str, object] = {}

    monkeypatch.setattr("driftbuild.build.build", lambda *_args: result)
    monkeypatch.setattr("driftbuild.build.build_timing_render", lambda _timing: "timing")

    def run_fake(command, *, cwd, environment, check):
        observed["command"] = command
        observed["cwd"] = cwd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("driftbuild.runner.run", run_fake)

    return_code = build_and_run(project, tmp_path, tmp_path / ".drift", BuildConfig("win32"))

    assert return_code == 0
    assert observed["command"] == (str(executable.resolve()),)
    assert observed["cwd"] == executable.parent.resolve()


def test_custom_run_command_expands_built_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "build" / "server.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    project = ProjectSpec(
        "sample",
        targets=(
            TargetSpec(
                "server",
                "custom",
                outputs=(Path("server.exe"),),
                action=ActionSpec(("build-server",), (Path("server.exe"),)),
                run_command=("{out}",),
            ),
        ),
        defaults=(TargetRef("server"),),
    )
    result = SimpleNamespace(
        timing=object(),
        generated=SimpleNamespace(outputs={"server": (executable,)}, ninja_file=tmp_path / "build/build.ninja"),
    )
    observed: dict[str, object] = {}
    monkeypatch.setattr("driftbuild.build.build", lambda *_args: result)
    monkeypatch.setattr("driftbuild.build.build_timing_render", lambda _timing: "timing")

    def run_fake(command, *, cwd, environment, check):
        observed["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("driftbuild.runner.run", run_fake)

    assert build_and_run(project, tmp_path, tmp_path / ".drift", BuildConfig("win32")) == 0
    assert observed["command"] == (str(executable.resolve()),)
