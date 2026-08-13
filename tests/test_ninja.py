import json
from pathlib import Path

import pytest

from driftbuild.api import ActionSpec, BuildConfig, PoolSpec, ProjectApi, ProjectSpec, TargetRef
from driftbuild.errors import ConfigurationError
from driftbuild.model import TargetDependency, TargetSpec
from driftbuild.ninja import generate
from driftbuild.toolchain import Toolchain


def test_msvc_configuration_selects_matching_dynamic_runtime(tmp_path: Path) -> None:
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("win32", compiler="msvc", build_type="debug"))
    project = api.project("sample", defaults=(api.executable("app", sources=api.files("main.c")),))
    toolchain = Toolchain("msvc", "cl", "cl", "link", "lib", {}, ".obj", ".exe", "", ".lib", "", ".dll")

    debug = generate(project, tmp_path, tmp_path / "debug", api.config, toolchain)
    release_config = BuildConfig("win32", compiler="msvc", build_type="release")
    release = generate(project, tmp_path, tmp_path / "release", release_config, toolchain)

    assert "/MDd" in debug.compilation_database.read_text(encoding="utf-8")
    assert "/MD " in release.compilation_database.read_text(encoding="utf-8")


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


def test_executable_stages_transitive_runtime_and_sets_loader_path(tmp_path: Path) -> None:
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    runtime = tmp_path / "libsample.so"
    runtime.write_bytes(b"shared")
    dependency = TargetSpec("sample", "static_library", runtime_files=(runtime,))
    app = TargetSpec(
        "app",
        "executable",
        sources=(Path("main.c"),),
        dependencies=(TargetDependency(TargetRef("sample"), "private"),),
    )
    project = ProjectSpec("fixture", (dependency, app), (TargetRef("app"),))
    config = BuildConfig("linux", compiler="gcc")
    toolchain = Toolchain("gcc", "gcc", "g++", "g++", "ar", {}, ".o", "", "lib", ".a", "lib", ".so")

    generated = generate(project, tmp_path, tmp_path / ".drift", config, toolchain)
    ninja = generated.ninja_file.read_text(encoding="utf-8")

    assert "-Wl,-rpath,$$ORIGIN" in ninja
    assert "description = RUNTIME app" in ninja
    assert runtime.name in ninja
    assert "runtime/app.stamp" in ninja.replace("$:", ":").replace("\\", "/")


def test_component_alias_propagates_each_library_interface(tmp_path: Path) -> None:
    for name in ("first", "second"):
        (tmp_path / f"{name}.c").write_text(f"int {name}(void) {{ return 1; }}\n", encoding="utf-8")
        (tmp_path / name).mkdir()
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    first = TargetSpec("first", "static_library", sources=(Path("first.c"),), include_dirs=(Path("first"),))
    second = TargetSpec("second", "static_library", sources=(Path("second.c"),), include_dirs=(Path("second"),))
    components = TargetSpec(
        "components",
        "alias",
        objects=(TargetRef("first"), TargetRef("second")),
    )
    app = TargetSpec(
        "app",
        "executable",
        sources=(Path("main.c"),),
        dependencies=(TargetDependency(TargetRef("components"), "private"),),
    )
    project = ProjectSpec("fixture", (first, second, components, app), (TargetRef("app"),))
    config = BuildConfig("linux", compiler="gcc")
    toolchain = Toolchain("gcc", "gcc", "g++", "g++", "ar", {}, ".o", "", "lib", ".a", "lib", ".so")

    ninja = generate(project, tmp_path, tmp_path / ".drift", config, toolchain).ninja_file.read_text(encoding="utf-8")

    assert f"-I{tmp_path / 'first'}" in ninja
    assert f"-I{tmp_path / 'second'}" in ninja
    link = next(
        line for line in ninja.replace("\\", "/").splitlines() if line.startswith("build ") and "bin/app" in line
    )
    assert "libfirst.a" in link
    assert "libsecond.a" in link


def test_runtime_bundle_rejects_colliding_file_names(tmp_path: Path) -> None:
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    first = (tmp_path / "first")
    second = (tmp_path / "second")
    first.mkdir()
    second.mkdir()
    (first / "sample.dll").write_bytes(b"first")
    (second / "sample.dll").write_bytes(b"second")
    dependencies = (
        TargetSpec("first", "static_library", runtime_files=(first / "sample.dll",)),
        TargetSpec("second", "static_library", runtime_files=(second / "sample.dll",)),
    )
    app = TargetSpec(
        "app",
        "executable",
        sources=(Path("main.c"),),
        dependencies=tuple(TargetDependency(TargetRef(target.name), "private") for target in dependencies),
    )
    project = ProjectSpec("fixture", (*dependencies, app), (TargetRef("app"),))
    toolchain = Toolchain("msvc", "cl", "cl", "link", "lib", {}, ".obj", ".exe", "", ".lib", "", ".dll")

    with pytest.raises(ConfigurationError, match="Runtime files collide"):
        generate(project, tmp_path, tmp_path / ".drift", BuildConfig("win32", compiler="msvc"), toolchain)


def test_runtime_already_beside_executable_is_not_copied(tmp_path: Path) -> None:
    (tmp_path / "library.c").write_text("int value(void) { return 1; }\n", encoding="utf-8")
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    library = TargetSpec("library", "shared_library", sources=(Path("library.c"),))
    app = TargetSpec(
        "app",
        "executable",
        sources=(Path("main.c"),),
        dependencies=(TargetDependency(TargetRef("library"), "private"),),
    )
    project = ProjectSpec("fixture", (library, app), (TargetRef("app"),))
    toolchain = Toolchain("msvc", "cl", "cl", "link", "lib", {}, ".obj", ".exe", "", ".lib", "", ".dll")

    ninja = generate(
        project, tmp_path, tmp_path / ".drift", BuildConfig("win32", compiler="msvc"), toolchain
    ).ninja_file.read_text(encoding="utf-8")

    assert "RUNTIME app" not in ninja
