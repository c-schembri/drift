import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from driftbuild.cli import (
    _base_parser,
    _completion,
    _configuration,
    _output,
    _project_bootstrap,
    _project_directory_normalize,
    _provider_command,
    _run,
    _sdk,
)
from driftbuild.model import BuildConfig, CommandGroupSpec, CommandSpec, LocalSdkSpec, ProjectSpec, TargetRef


def test_project_directory_after_command_becomes_root(tmp_path: Path) -> None:
    project = tmp_path / "sample"
    project.mkdir()
    (project / "drift.toml").write_text("[project]\n", encoding="utf-8")

    normalized = _project_directory_normalize(["--compiler", "msvc", "build", str(project), "app"])

    assert normalized[:2] == ["--root", str(project.resolve())]
    assert normalized[2:] == ["--compiler", "msvc", "build", "app"]


def test_sdk_materialize_command_selects_named_sdk(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "header.h").write_text("header", encoding="utf-8")
    descriptor = tmp_path / "sdk.json"
    descriptor.write_text('{"materialize":{"required":["header.h"]}}', encoding="utf-8")
    sdk = LocalSdkSpec("sample", source, tmp_path / "destination", descriptor)

    assert (
        _sdk(
            SimpleNamespace(names=["sample"]), ProjectSpec("sample", local_sdks=(sdk,)), tmp_path, BuildConfig("win32")
        )
        == 0
    )
    assert (tmp_path / "destination/header.h").read_text(encoding="utf-8") == "header"
    assert capsys.readouterr().out == "Materialized sample: 1 files\n"


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


def test_provider_command_can_receive_unparsed_arguments(tmp_path: Path) -> None:
    received: tuple[str, ...] = ()

    def handler(_context, values):  # type: ignore[no-untyped-def]
        nonlocal received
        received = values
        return 3

    project = ProjectSpec(
        "sample",
        commands=(CommandSpec(("control",), "Control services", handler, passthrough=True),),
    )
    arguments = argparse.Namespace(arguments=["control", "server", "--backend", "systemd"], verbose=False)

    assert _provider_command(arguments, project, tmp_path, BuildConfig("test")) == 3
    assert received == ("server", "--backend", "systemd")


def test_provider_command_can_import_from_project_root(tmp_path: Path) -> None:
    (tmp_path / "project_helper.py").write_text("VALUE = 7\n", encoding="utf-8")

    def handler(_context, _values):  # type: ignore[no-untyped-def]
        from project_helper import VALUE

        return VALUE

    project = ProjectSpec(
        "sample",
        commands=(CommandSpec(("check",), "Check project", handler, passthrough=True),),
    )
    arguments = argparse.Namespace(arguments=["check"], verbose=False)

    assert _provider_command(arguments, project, tmp_path, BuildConfig("test")) == 7
    assert sys.path[0] != str(tmp_path)


def test_provider_command_builds_targets_and_exposes_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "sample.exe"
    received = None

    def handler(context, _values):  # type: ignore[no-untyped-def]
        nonlocal received
        received = context.outputs

    project = ProjectSpec(
        "sample",
        commands=(CommandSpec(("deploy",), "Deploy", handler, build_targets=(TargetRef("sample"),)),),
    )
    generated = SimpleNamespace(outputs={"sample": (output,)})
    monkeypatch.setattr("driftbuild.build.build", lambda *_args, **_kwargs: SimpleNamespace(generated=generated))
    arguments = argparse.Namespace(arguments=["deploy"], verbose=False)

    assert _provider_command(arguments, project, tmp_path, BuildConfig("test")) == 0
    assert received == {"sample": (output,)}


def test_provider_group_help_lists_only_direct_children(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = ProjectSpec(
        "sample",
        commands=(
            CommandSpec(("release", "publish"), "Publish a release", lambda *_args: 0),
            CommandSpec(("release", "sign", "verify"), "Verify a signature", lambda *_args: 0),
        ),
        command_groups=(
            CommandGroupSpec(("release",), "Release workflows"),
            CommandGroupSpec(("release", "sign"), "Signing workflows"),
        ),
    )
    arguments = argparse.Namespace(arguments=["release", "--help"], verbose=False)

    assert _provider_command(arguments, project, tmp_path, BuildConfig("test")) == 0
    output = capsys.readouterr().out
    assert "drift command release: Release workflows" in output
    assert "publish" in output
    assert "sign" in output
    assert "verify" not in output


def test_root_provider_help_is_forwarded_to_the_declared_tree() -> None:
    arguments = _base_parser().parse_args(["command", "--help"])

    assert arguments.provider_help is True
    assert arguments.arguments == []


def test_completion_includes_provider_command_tree_words(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = ProjectSpec(
        "sample",
        commands=(CommandSpec(("release", "publish"), "Publish a release", lambda *_args: 0),),
        command_groups=(CommandGroupSpec(("release",), "Release workflows"),),
    )

    assert _completion(argparse.Namespace(shell="bash"), project, tmp_path, BuildConfig("test")) == 0
    output = capsys.readouterr().out
    assert "complete -F _drift_complete drift" in output
    assert "publish" in output
    assert "release" in output


def test_output_prints_configured_target_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts = (tmp_path / "first.exe", tmp_path / "second.exe")
    configured = SimpleNamespace(generated=SimpleNamespace(outputs={"servers": artifacts}))
    monkeypatch.setattr("driftbuild.build.configure", lambda *_args: configured)

    assert (
        _output(
            argparse.Namespace(target_name="servers", json=True), ProjectSpec("sample"), tmp_path, BuildConfig("test")
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out) == [str(path.resolve()) for path in artifacts]


def test_output_target_name_does_not_replace_cross_compile_target(tmp_path: Path) -> None:
    arguments = _base_parser().parse_args(["output", "integration-server"])

    config = _configuration(arguments, tmp_path)

    assert config.target is None
    assert arguments.target_name == "integration-server"


def test_project_bootstrap_installs_an_exact_required_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "drift.toml").write_text('[project]\nrequires-drift = "==99.0.0"\n', encoding="utf-8")
    monkeypatch.setattr(
        "driftbuild.self_update.self_update",
        lambda repository, version, _signers, _signer: (version, tmp_path / repository / "drift"),
    )

    assert (
        _project_bootstrap(
            argparse.Namespace(install=True, repository="owner/drift"),
            ProjectSpec("sample"),
            tmp_path,
            BuildConfig("test"),
        )
        == 0
    )

    assert "Installed project-required Drift 99.0.0" in capsys.readouterr().out
