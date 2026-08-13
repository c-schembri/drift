import argparse
from pathlib import Path

import pytest

from driftbuild.cli import _project_directory_normalize, _run
from driftbuild.model import BuildConfig, ProjectSpec


def test_project_directory_after_command_becomes_root(tmp_path: Path) -> None:
    project = tmp_path / "sample"
    project.mkdir()
    (project / "drift.toml").write_text("[project]\n", encoding="utf-8")

    normalized = _project_directory_normalize(["--compiler", "msvc", "build", str(project), "app"])

    assert normalized[:2] == ["--root", str(project.resolve())]
    assert normalized[2:] == ["--compiler", "msvc", "build", "app"]


def test_target_directory_without_manifest_remains_a_target(tmp_path: Path) -> None:
    target = tmp_path / "assets"
    target.mkdir()

    assert _project_directory_normalize(["build", str(target)]) == ["build", str(target)]


def test_visual_studio_accepts_project_directory(tmp_path: Path) -> None:
    project = tmp_path / "sample"
    project.mkdir()
    (project / "drift.toml").write_text("[project]\n", encoding="utf-8")

    normalized = _project_directory_normalize(["generate", "visual-studio", str(project)])

    assert normalized == ["--root", str(project.resolve()), "generate", "visual-studio"]


def test_run_accepts_project_directory_before_target(tmp_path: Path) -> None:
    project = tmp_path / "sample"
    project.mkdir()
    (project / "drift.toml").write_text("[project]\n", encoding="utf-8")

    normalized = _project_directory_normalize(["run", str(project), "app", "--", "argument"])

    assert normalized == ["--root", str(project.resolve()), "run", "app", "--", "argument"]


def test_lock_accepts_project_directory(tmp_path: Path) -> None:
    project = tmp_path / "sample"
    project.mkdir()
    (project / "drift.toml").write_text("[project]\n", encoding="utf-8")

    normalized = _project_directory_normalize(["lock", str(project)])

    assert normalized == ["--root", str(project.resolve()), "lock"]


def test_lock_accepts_project_directory_after_flag(tmp_path: Path) -> None:
    project = tmp_path / "sample"
    project.mkdir()
    (project / "drift.toml").write_text("[project]\n", encoding="utf-8")

    normalized = _project_directory_normalize(["lock", "--check", str(project)])

    assert normalized == ["--root", str(project.resolve()), "lock", "--check"]


def test_run_separates_target_from_program_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    received: tuple[str | None, tuple[str, ...]] | None = None

    def fake_build_and_run(
        _project: ProjectSpec,
        _root: Path,
        _state_root: Path,
        _config: BuildConfig,
        target: str | None,
        arguments: tuple[str, ...],
    ) -> int:
        nonlocal received
        received = target, tuple(arguments)
        return 7

    monkeypatch.setattr("driftbuild.runner.build_and_run", fake_build_and_run)
    arguments = argparse.Namespace(arguments=["app", "--", "one", "two"])

    assert _run(arguments, ProjectSpec("sample"), tmp_path, BuildConfig("win32")) == 7
    assert received == ("app", ("one", "two"))
