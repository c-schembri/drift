import shutil
import sys
from pathlib import Path

import pytest

from driftbuild.api import BuildConfig
from driftbuild.bootstrap import ninja_resolve
from driftbuild.cmake import project_import


@pytest.fixture(autouse=True)
def _installed_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    cmake = shutil.which("cmake")
    if cmake is None:
        pytest.skip("cmake is unavailable")
    repository = Path(__file__).parents[1]
    monkeypatch.setenv("DRIFT_CMAKE", cmake)
    monkeypatch.setenv("DRIFT_NINJA", str(ninja_resolve(repository / ".drift")))


def test_cmake_file_api_imports_buildable_graph_and_default(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "include").mkdir(parents=True)
    (source / "include" / "sample.h").write_text("int sample(void);\n", encoding="utf-8")
    (source / "sample.c").write_text("int sample(void) { return 1; }\n", encoding="utf-8")
    (source / "CMakeLists.txt").write_text(
        """cmake_minimum_required(VERSION 3.20)
project(sample C)
add_library(sample SHARED sample.c)
target_include_directories(sample PUBLIC include)
if(NOT WIN32)
  target_compile_options(sample PRIVATE "-x" "c-header")
  target_link_options(sample PRIVATE "-Wl,--no-undefined")
  target_link_libraries(sample PRIVATE m)
endif()
""",
        encoding="utf-8",
    )

    project = project_import(source, tmp_path / "state", BuildConfig(sys.platform), "sample")

    assert project.defaults[0].name == "sample"
    assert len(project.targets) == 1
    target = project.targets[0]
    assert target.kind == "external_library"
    assert target.action is not None
    assert "--build" in target.action.command
    assert (source / "include").resolve() in target.include_dirs
    assert target.outputs
    assert target.compile_arguments == ()
    if sys.platform != "win32":
        assert "-lm" in target.link_arguments
        assert all("no-undefined" not in value for value in target.link_arguments)


def test_cmake_file_api_configuration_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
