import json
from pathlib import Path

from driftbuild.api import ActionSpec, BuildConfig, PoolSpec, ProjectApi, ProjectSpec, TargetRef
from driftbuild.model import TargetDependency, TargetSpec
from driftbuild.ninja import generate
from driftbuild.toolchain import Toolchain


def test_generation_is_stable_and_emits_compilation_database(tmp_path: Path) -> None:
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "value.h").write_text("int value(void);", encoding="utf-8")
    (tmp_path / "value.c").write_text('#include "value.h"\nint value(void) { return 42; }', encoding="utf-8")
    (tmp_path / "main.cpp").write_text("int main() { return 0; }", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("linux", compiler="gcc"))
    library = api.static_library(
        "value", sources=api.files("value.c"), include_dirs=("include",), defines=("VALUE=42",)
    )
    app = api.executable("app", sources=api.files("main.cpp"), dependencies=(api.private(library),))
    project = api.project("fixture", defaults=(app,))
    toolchain = Toolchain("gcc", "gcc", "g++", "g++", "ar", {}, ".o", "", "lib", ".a", "lib", ".so")
    build_root = tmp_path / ".drift" / "build"

    first = generate(project, tmp_path, build_root, api.config, toolchain)
    before = first.ninja_file.stat().st_mtime_ns
    second = generate(project, tmp_path, build_root, api.config, toolchain)

    assert second.ninja_file.stat().st_mtime_ns == before
    ninja = second.ninja_file.read_text(encoding="utf-8")
    assert "build app: phony" in ninja
    assert "VALUE=42" in ninja
    database = json.loads(second.compilation_database.read_text(encoding="utf-8"))
    assert {Path(entry["file"]).name for entry in database} == {"value.c", "main.cpp"}


def test_msvc_shared_library_emits_runtime_and_import_library(tmp_path: Path) -> None:
    (tmp_path / "library.cpp").write_text("int value() { return 42; }", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("win32", compiler="msvc"))
    library = api.shared_library("sample", sources=api.files("library.cpp"))
    project = api.project("fixture", defaults=(library,))
    toolchain = Toolchain("msvc", "cl", "cl", "link", "lib", {}, ".obj", ".exe", "", ".lib", "", ".dll")

    generated = generate(project, tmp_path, tmp_path / ".drift", api.config, toolchain)
    ninja = generated.ninja_file.read_text(encoding="utf-8")

    assert len(generated.outputs["sample"]) == 2
    assert "/DLL" in ninja
    assert "/IMPLIB:" in ninja
    assert "rspfile = $response_file" in ninja
    assert "rspfile_content = $in $link_arguments" in ninja
    assert "sample-link.rsp" in ninja


def test_msvc_static_library_places_inputs_in_response_file(tmp_path: Path) -> None:
    (tmp_path / "first.c").write_text("int first(void) { return 1; }", encoding="utf-8")
    (tmp_path / "second.c").write_text("int second(void) { return 2; }", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("win32", compiler="msvc"))
    library = api.static_library("sample", sources=api.files("first.c", "second.c"))
    project = api.project("fixture", defaults=(library,))
    toolchain = Toolchain("msvc", "cl", "cl", "link", "lib", {}, ".obj", ".exe", "", ".lib", "", ".dll")

    generated = generate(project, tmp_path, tmp_path / ".drift", api.config, toolchain)
    ninja = generated.ninja_file.read_text(encoding="utf-8")
    archive_command = next(line for line in ninja.splitlines() if line.startswith("  tool_command = lib "))

    assert "first.obj" not in archive_command
    assert "second.obj" not in archive_command
    assert "sample-archive.rsp" in ninja


def test_custom_action_emits_pool_and_dependency_inputs(tmp_path: Path) -> None:
    (tmp_path / "input.txt").write_text("input", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("linux", compiler="gcc"))
    api.pool(PoolSpec("generator", 1))
    action = api.command_action(
        ("tool", "{in:0}", "{out}"),
        outputs=("generated.txt",),
        inputs=api.files("input.txt"),
        pool="generator",
        restat=True,
    )
    generated_target = api.custom_target("generated", action)
    project = api.project("fixture", defaults=(generated_target,))
    toolchain = Toolchain("gcc", "gcc", "g++", "g++", "ar", {}, ".o", "", "lib", ".a", "lib", ".so")

    generated = generate(project, tmp_path, tmp_path / ".drift", api.config, toolchain)
    ninja = generated.ninja_file.read_text(encoding="utf-8")

    assert "pool generator\n  depth = 1" in ninja
    assert "pool = generator" in ninja
    assert "input.txt" in ninja


def test_objective_c_source_is_compiled_with_c_compiler(tmp_path: Path) -> None:
    (tmp_path / "window.m").write_text("int window(void) { return 1; }", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("darwin", compiler="clang"))
    library = api.static_library("window", sources=api.files("window.m"))
    project = api.project("fixture", defaults=(library,))
    toolchain = Toolchain("clang", "clang", "clang++", "clang++", "ar", {}, ".o", "", "lib", ".a", "lib", ".dylib")

    generated = generate(project, tmp_path, tmp_path / ".drift", api.config, toolchain)
    database = json.loads(generated.compilation_database.read_text(encoding="utf-8"))

    assert database[0]["command"].startswith("clang ")
    assert database[0]["file"].endswith("window.m")


def test_external_action_orders_consumer_compilation_without_linking_stamp(tmp_path: Path) -> None:
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    stamp = tmp_path / "installed.stamp"
    package = TargetSpec(
        "package",
        "external_library",
        outputs=(stamp,),
        action=ActionSpec(("install-package",), (stamp,)),
    )
    app = TargetSpec(
        "app",
        "executable",
        sources=(Path("main.c"),),
        dependencies=(TargetDependency(TargetRef("package"), "private"),),
    )
    project = ProjectSpec("fixture", (package, app), (TargetRef("app"),))
    config = BuildConfig("linux", compiler="gcc")
    toolchain = Toolchain("gcc", "gcc", "g++", "g++", "ar", {}, ".o", "", "lib", ".a", "lib", ".so")

    generated = generate(project, tmp_path, tmp_path / ".drift", config, toolchain)
    ninja = generated.ninja_file.read_text(encoding="utf-8")
    compile_edge = next(line for line in ninja.splitlines() if ": cc " in line)
    link_edge = next(line for line in ninja.splitlines() if ": link " in line)

    assert "|| " in compile_edge and stamp.name in compile_edge
    assert "|| " in link_edge and stamp.name in link_edge
    assert str(stamp) not in next(line for line in ninja.splitlines() if line.startswith("  command = g++"))
