import sys
from pathlib import Path

from driftbuild.api import BuildConfig, ProjectApi
from driftbuild.meson import project_import
from driftbuild.process import run


def test_meson_introspection_imports_buildable_graph(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "source"
    (source / "include").mkdir(parents=True)
    (source / "include" / "sample.h").write_text("int sample(void);\n", encoding="utf-8")
    (source / "sample.c").write_text("int sample(void) { return 1; }\n", encoding="utf-8")
    (source / "meson.build").write_text(
        """project('sample', 'c')
library('sample', 'sample.c', include_directories: include_directories('include'))
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("DRIFT_MESON", raising=False)

    project = project_import(source, tmp_path / "state", BuildConfig(sys.platform), "sample")

    assert project.defaults[0].name == "sample"
    assert len(project.targets) == 1
    target = project.targets[0]
    assert target.kind == "external_library"
    assert target.action is not None
    assert "-C" in target.action.command
    assert (source / "include").resolve() in target.include_dirs
    assert target.outputs
    run(target.action.command, environment=target.action.environment, capture=True)
    assert all(output.is_file() for output in target.outputs)


def test_meson_package_linkage_selects_static_default(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.c").write_text("int sample(void) { return 1; }\n", encoding="utf-8")
    (source / "meson.build").write_text(
        "project('sample', 'c')\nlibrary('sample', 'sample.c')\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DRIFT_MESON", raising=False)
    api = ProjectApi(tmp_path, BuildConfig(sys.platform))
    api.package("sample", source=api.git(str(source), "1" * 40), linkage="static")
    package = api.project("consumer").packages[0]

    imported = project_import(source, tmp_path / "state", api.config, package)

    outputs = imported.targets[0].outputs
    assert any(path.suffix.casefold() in (".a", ".lib") for path in outputs)
