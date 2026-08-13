from pathlib import Path

import pytest

from driftbuild.api import API_VERSION, BuildConfig, ProjectApi
from driftbuild.errors import ConfigurationError


def api_for(root: Path) -> ProjectApi:
    return ProjectApi(root, BuildConfig("test"))


def test_files_and_tree_are_root_confined_and_sorted(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "z.cpp").write_text("", encoding="utf-8")
    (tmp_path / "source" / "a.cpp").write_text("", encoding="utf-8")
    (tmp_path / "source" / "skip.txt").write_text("", encoding="utf-8")

    api = api_for(tmp_path)

    assert api.files("source/a.cpp").files == (Path("source/a.cpp"),)
    assert api.tree("source", include=("*.cpp",)).files == (Path("source/a.cpp"), Path("source/z.cpp"))
    with pytest.raises(ConfigurationError, match="escapes"):
        api.files("../outside.cpp")


def test_public_and_private_dependencies_are_explicit(tmp_path: Path) -> None:
    source = tmp_path / "source.c"
    source.write_text("int value;", encoding="utf-8")
    api = api_for(tmp_path)

    library = api.static_library("library", sources=api.files("source.c"), include_dirs=("include",))
    application = api.executable("application", sources=api.files("source.c"), dependencies=(api.private(library),))
    project = api.project("sample", defaults=(application,))

    assert project.targets[1].dependencies[0].visibility == "private"  # type: ignore[union-attr]
    assert api.output(application).path == Path("application")


def test_project_api_exposes_stable_version(tmp_path: Path) -> None:
    api = api_for(tmp_path)

    assert API_VERSION == 1
    assert api.api_version == 1
