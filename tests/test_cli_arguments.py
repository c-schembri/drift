from pathlib import Path

from driftbuild.cli import _project_directory_normalize


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
