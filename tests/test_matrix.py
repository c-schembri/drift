from pathlib import Path
from types import SimpleNamespace

from driftbuild.matrix import matrix_run
from driftbuild.model import BuildConfig, MatrixSpec, ProjectSpec


def test_matrix_evaluates_and_builds_each_configuration(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    configured: list[BuildConfig] = []
    built: list[tuple[BuildConfig, tuple[str, ...], int | None]] = []

    def project_load(_root: Path, config: BuildConfig) -> ProjectSpec:
        configured.append(config)
        return ProjectSpec("sample")

    def build(
        _project: ProjectSpec,
        _root: Path,
        _state_root: Path,
        config: BuildConfig,
        targets: tuple[str, ...],
        *,
        jobs: int | None,
    ):  # type: ignore[no-untyped-def]
        built.append((config, targets, jobs))
        return SimpleNamespace(timing=object())

    monkeypatch.setattr("driftbuild.matrix.project_load", project_load)
    monkeypatch.setattr("driftbuild.matrix.packages_compose", lambda project, *_args, **_kwargs: project)
    monkeypatch.setattr("driftbuild.matrix.project_validate", lambda _project: {})
    monkeypatch.setattr("driftbuild.matrix.build", build)
    monkeypatch.setattr("driftbuild.matrix.build_timing_render", lambda _timing: "timing")

    matrix_run(
        MatrixSpec(
            "client",
            (("build-type", ("debug", "release")), ("flavor", ("developer", "retail"))),
            targets=("app",),
        ),
        tmp_path,
        tmp_path / ".drift",
        BuildConfig("win32"),
        jobs=3,
    )

    assert [(config.build_type, config.values["flavor"]) for config in configured] == [
        ("debug", "developer"),
        ("debug", "retail"),
        ("release", "developer"),
        ("release", "retail"),
    ]
    assert all(targets == ("app",) and jobs == 3 for _config, targets, jobs in built)
    assert capsys.readouterr().out.count("timing") == 4
