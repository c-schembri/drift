import shutil
import sys
from pathlib import Path

import pytest

from driftbuild.api import BuildConfig
from driftbuild.cmake import project_import


def test_cmake_file_api_imports_buildable_graph_and_default(tmp_path: Path) -> None:
    if shutil.which("cmake") is None:
        pytest.skip("cmake is unavailable")
    source = tmp_path / "source"
    (source / "include").mkdir(parents=True)
    (source / "include" / "sample.h").write_text("int sample(void);\n", encoding="utf-8")
    (source / "sample.c").write_text("int sample(void) { return 1; }\n", encoding="utf-8")
    (source / "CMakeLists.txt").write_text(
        """cmake_minimum_required(VERSION 3.20)
project(sample C)
add_library(sample SHARED sample.c)
target_include_directories(sample PUBLIC include)
""",
        encoding="utf-8",
    )

    project = project_import(source, tmp_path / "state", BuildConfig(sys.platform), "sample")

    assert project.defaults[0].name == "sample"
    assert len(project.targets) == 1
    target = project.targets[0]
    assert target.kind == "external_library"
    assert target.action is not None
    assert target.action.command[1] == "--build"
    assert (source / "include").resolve() in target.include_dirs
    assert target.outputs


def test_cmake_file_api_configuration_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which("cmake") is None:
        pytest.skip("cmake is unavailable")
    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.c").write_text("int sample(void) { return 1; }\n", encoding="utf-8")
    (source / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20)\nproject(sample C)\nadd_library(sample STATIC sample.c)\n",
        encoding="utf-8",
    )
    state = tmp_path / "state"
    config = BuildConfig(sys.platform)
    project_import(source, state, config, "sample")

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cached CMake import configured again")

    monkeypatch.setattr("driftbuild.cmake.run", unexpected)
    cached = project_import(source, state, config, "sample")

    assert cached.defaults[0].name == "sample"
