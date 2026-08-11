import xml.etree.ElementTree as xml
from pathlib import Path

import pytest

from driftbuild.api import BuildConfig, ProjectApi
from driftbuild.errors import ConfigurationError
from driftbuild.visual_studio import generate

_NAMESPACE = {"msbuild": "http://schemas.microsoft.com/developer/msbuild/2003"}


def _project(root: Path, build_type: str = "debug", *, extra: bool = False):
    api = ProjectApi(root, BuildConfig("win32", compiler="msvc", build_type=build_type))
    library = api.static_library(
        "math",
        sources=api.files("src/add.c"),
        public_headers=api.files("include/add.h"),
        include_dirs=("include",),
        defines=("MATH_ENABLED=1",),
    )
    application = api.executable(
        "hello",
        sources=api.files("src/main.cpp"),
        dependencies=(api.private(library),),
    )
    if extra:
        api.alias("extra", (application,))
    return api.project("sample", defaults=(application,))


def test_generates_makefile_projects_solution_and_filters(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "include").mkdir()
    (tmp_path / "src" / "add.c").write_text("int add(void) { return 42; }", encoding="utf-8")
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }", encoding="utf-8")
    (tmp_path / "include" / "add.h").write_text("int add(void);", encoding="utf-8")
    projects = {"debug": _project(tmp_path), "release": _project(tmp_path, "release")}
    output = tmp_path / ".drift" / "visual-studio"

    result = generate(projects, tmp_path, output, "x86_64", "hello", {"FEATURE": "some value"})
    solution_before = result.solution.stat().st_mtime_ns
    second = generate(projects, tmp_path, output, "x86_64", "hello", {"FEATURE": "some value"})

    assert second.solution.stat().st_mtime_ns == solution_before
    solution = result.solution.read_text(encoding="utf-8")
    assert solution.index('= "hello"') < solution.index('= "math"')
    assert '"sample (build)", "sample-build.vcxproj"' in solution
    assert solution.count(".Build.0 =") == 2

    project = xml.parse(result.projects["hello"])
    assert project.findtext(".//msbuild:ConfigurationType", namespaces=_NAMESPACE) == "Makefile"
    commands = [element.text or "" for element in project.findall(".//msbuild:NMakeBuildCommandLine", _NAMESPACE)]
    assert all("drift --root" in command and 'build "hello"' in command for command in commands)
    assert all('-D "FEATURE=some value"' in command for command in commands)
    rebuild = project.findtext(".//msbuild:NMakeReBuildCommandLine", namespaces=_NAMESPACE)
    assert rebuild is not None and " && " in rebuild
    includes = project.findtext(".//msbuild:NMakeIncludeSearchPath", namespaces=_NAMESPACE)
    defines = project.findtext(".//msbuild:NMakePreprocessorDefinitions", namespaces=_NAMESPACE)
    assert includes is not None and "include" in includes
    assert defines == "MATH_ENABLED=1"
    assert project.find(".//msbuild:LocalDebuggerCommand", _NAMESPACE) is not None
    reference = project.find(".//msbuild:ProjectReference", _NAMESPACE)
    assert reference is not None and reference.attrib["Include"] == "math.vcxproj"

    filters = xml.parse(result.projects["hello"].with_suffix(".vcxproj.filters"))
    assert filters.find(".//msbuild:Filter[@Include='src']", _NAMESPACE) is not None
    assert filters.find(".//msbuild:ClCompile/msbuild:Filter", _NAMESPACE).text == "src"  # type: ignore[union-attr]


def test_rejects_configuration_target_drift(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "include").mkdir()
    for path in ("src/add.c", "src/main.cpp", "include/add.h"):
        (tmp_path / path).write_text("", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="identical target names and kinds"):
        generate(
            {"debug": _project(tmp_path), "release": _project(tmp_path, "release", extra=True)},
            tmp_path,
            tmp_path / "visual-studio",
            "x86_64",
        )
