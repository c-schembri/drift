import sys
from pathlib import Path
from types import ModuleType

import pytest

from driftbuild.api import API_VERSION, BuildConfig, CommandGroupSpec, Deployment, MatrixSpec, ProjectApi
from driftbuild.errors import ConfigurationError
from driftbuild.project import project_provider_files


def api_for(root: Path) -> ProjectApi:
    return ProjectApi(root, BuildConfig("test"))


def test_provider_files_ignore_frozen_runtime_pseudo_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "drift.toml"
    manifest.write_text("[project]\n", encoding="utf-8")
    module = ModuleType("pyinstaller_pseudo_module")
    module.__file__ = "pyimod01_archive.py"
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.chdir(tmp_path)

    assert project_provider_files(tmp_path) == (manifest,)


def test_files_and_tree_are_root_confined_and_sorted(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "z.cpp").write_text("", encoding="utf-8")
    (tmp_path / "source" / "a.cpp").write_text("", encoding="utf-8")
    (tmp_path / "source" / "skip.txt").write_text("", encoding="utf-8")

    api = api_for(tmp_path)

    assert api.files("source/a.cpp").files == (Path("source/a.cpp"),)
    assert api.tree("source", include=("*.cpp",)).files == (Path("source/a.cpp"), Path("source/z.cpp"))
    project = api.project("sample")
    assert project.discovery_directories == (tmp_path / "source",)
    with pytest.raises(ConfigurationError, match="escapes"):
        api.files("../outside.cpp")
    with pytest.raises(ConfigurationError, match="pattern escapes"):
        api.tree("source", include=("../*.cpp",))


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


def test_cargo_declares_workspace_build_with_discovered_inputs(tmp_path: Path) -> None:
    server = tmp_path / "Server"
    (server / "src").mkdir(parents=True)
    (server / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (server / "Cargo.lock").write_text("", encoding="utf-8")
    (server / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("test", build_type="release"))

    target = api.cargo("server", manifest="Server/Cargo.toml", workspace=True)
    spec = api.project("sample", defaults=(target,)).targets[0]

    assert spec.action is not None
    assert spec.action.command[-10:] == (
        "--manifest",
        "Server/Cargo.toml",
        "--target-dir",
        "{build}",
        "--depfile",
        "{build}/cargo-deps/server.d",
        "--dep-target",
        "{out}",
        "--release",
        "--workspace",
    )
    assert spec.action.outputs == (Path("cargo-stamps/server.stamp"),)
    assert spec.action.stamp_outputs is True
    assert spec.action.inputs == (Path("Server/Cargo.toml"), Path("Server/Cargo.lock"))
    assert spec.action.depfile == Path("cargo-deps/server.d")


def test_cargo_rejects_workspace_and_package_selection(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    api = api_for(tmp_path)

    with pytest.raises(ConfigurationError, match="both a workspace and packages"):
        api.cargo("server", manifest="Cargo.toml", workspace=True, packages=("server",))


def test_cargo_static_library_requires_and_exposes_an_artifact(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='ffi'\nversion='0.1.0'\n", encoding="utf-8")
    api = api_for(tmp_path)

    target = api.cargo_static_library(
        "ffi",
        manifest="Cargo.toml",
        outputs=("debug/ffi.lib",),
    )
    spec = api.project("sample", defaults=(target,)).targets[0]

    assert spec.kind == "external_library"
    assert spec.outputs == (Path("debug/ffi.lib"),)


def test_cargo_run_target_is_exposed_to_drift_run(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='server'\nversion='0.1.0'\n", encoding="utf-8")
    api = api_for(tmp_path)

    target = api.cargo(
        "server",
        manifest="Cargo.toml",
        packages=("server",),
        targets=("server",),
        run_target="server",
    )
    spec = api.project("sample", defaults=(target,)).targets[0]

    assert spec.run_command == ("{out}",)
    assert spec.outputs == (Path("cargo-artifacts/server/server"),)


def test_cargo_can_share_a_repository_target_directory(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    api = api_for(tmp_path)

    target = api.cargo("server", manifest="Cargo.toml", workspace=True, target_directory="target")
    spec = api.project("sample", defaults=(target,)).targets[0]

    assert spec.action is not None
    target_index = spec.action.command.index("--target-dir")
    assert spec.action.command[target_index + 1] == str(tmp_path / "target")


def test_cargo_static_library_discovers_a_stable_artifact_output(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='ffi'\nversion='0.1.0'\n", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("win32"))

    target = api.cargo_static_library("ffi", manifest="Cargo.toml")
    spec = api.project("sample", defaults=(target,)).targets[0]

    assert spec.outputs == (Path("cargo-artifacts/ffi/ffi.lib"),)
    assert spec.action is not None
    assert "staticlib:ffi" in spec.action.command


def test_cargo_workspace_registers_conventional_checks(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    api = api_for(tmp_path)

    api.cargo_workspace("server", manifest="Cargo.toml", checks=("format", "test"))
    tests = api.project("sample").tests

    assert [test.name for test in tests] == ["server-format", "server"]
    assert tests[0].command[:2] == ("cargo", "fmt")
    assert tests[1].command[:2] == ("cargo", "test")


def test_command_groups_and_matrices_are_declared(tmp_path: Path) -> None:
    api = api_for(tmp_path)

    api.command_group(CommandGroupSpec(("release",), "Release workflows"))
    api.matrix(MatrixSpec("client", (("build-type", ("debug", "release")), ("flavor", ("developer",)))))
    project = api.project("sample")

    assert project.command_groups[0].path == ("release",)
    assert project.matrices[0].axes[0] == ("build-type", ("debug", "release"))
    with pytest.raises(ConfigurationError, match="matrix operation"):
        api.matrix(MatrixSpec("invalid", (("compiler", ("gcc",)),), operation="deploy"))  # type: ignore[arg-type]


def test_deploy_maps_runtime_input_to_relative_destination(tmp_path: Path) -> None:
    (tmp_path / "source.dll").write_bytes(b"runtime")
    api = api_for(tmp_path)

    deployment = api.deploy("source.dll", "plugins/source.dll")

    assert deployment.source == Path("source.dll")
    assert deployment.destination == Path("plugins/source.dll")
    with pytest.raises(ConfigurationError, match="relative file path"):
        api.deploy("source.dll", "../source.dll")


def test_dependency_accepts_deployed_external_runtime_file(tmp_path: Path) -> None:
    external = tmp_path.parent / "external.dll"
    external.write_bytes(b"runtime")
    api = api_for(tmp_path)

    dependency = api.dependency(
        "external",
        runtime_files=(api.deploy(external, "plugins/external.dll"),),
    )

    deployed = dependency.runtime_files[0]
    assert isinstance(deployed, Deployment)
    assert deployed.source == external.resolve()
    assert deployed.destination == Path("plugins/external.dll")


def test_project_options_are_typed_and_validated(tmp_path: Path) -> None:
    api = ProjectApi(tmp_path, BuildConfig("test", values={"flavor": "retail", "workers": "4"}))

    assert api.option("flavor", choices=("developer", "retail"), default="developer") == "retail"
    assert api.option("workers", value_type=int, default=1) == 4
    assert [option.name for option in api.project("sample").options] == ["flavor", "workers"]

    invalid = ProjectApi(tmp_path, BuildConfig("test", values={"flavor": "unknown"}))
    with pytest.raises(ConfigurationError, match="must be one of"):
        invalid.option("flavor", choices=("developer", "retail"), default="developer")

    with pytest.raises(ConfigurationError, match="default must be int"):
        ProjectApi(tmp_path, BuildConfig("test")).option("workers", value_type=int, default="four")


def test_provider_action_records_an_importable_handler(tmp_path: Path) -> None:
    api = api_for(tmp_path)
    handler = tmp_path / "build_tools.py"
    handler.write_text("def generate(arguments):\n    return 0\n", encoding="utf-8")

    action = api.provider_action("build_tools:generate", ("{in:0}", "{out}"), outputs=("generated.txt",))

    assert action.handler == "build_tools:generate"
    assert action.command == ("{in:0}", "{out}")
    assert action.implicit_inputs == (handler.resolve(),)


def test_deploy_tree_preserves_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "assets" / "nested").mkdir(parents=True)
    (tmp_path / "assets" / "one.txt").write_text("one", encoding="utf-8")
    (tmp_path / "assets" / "nested" / "two.txt").write_text("two", encoding="utf-8")
    api = api_for(tmp_path)

    deployments = api.deploy_tree(api.tree("assets"), "assets", "data")

    assert [item.destination.as_posix() for item in deployments] == ["data/nested/two.txt", "data/one.txt"]


def test_local_sdk_loads_selected_interface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = tmp_path / "sdk"
    (sdk / "include").mkdir(parents=True)
    (sdk / "lib").mkdir()
    (sdk / "bin").mkdir()
    (sdk / "lib" / "sample.lib").write_bytes(b"lib")
    (sdk / "bin" / "sample.dll").write_bytes(b"dll")
    descriptor = tmp_path / "sample.sdk.json"
    descriptor.write_text(
        '{"include_dirs":["include"],"libraries":["lib/sample.lib"],'
        '"runtime_files":["bin/sample.dll"],"defines":["SAMPLE=1"]}',
        encoding="utf-8",
    )
    api = api_for(tmp_path)
    monkeypatch.setenv("SAMPLE_SDK_ROOT", str(sdk))

    dependency = api.local_sdk(
        "sample", descriptor="sample.sdk.json", environment=("SAMPLE_SDK_ROOT",), roots=(tmp_path / "missing",)
    )

    assert dependency.root == sdk
    assert dependency.compile.include_dirs == (sdk / "include",)
    assert dependency.compile.defines == ("SAMPLE=1",)
    assert dependency.link.libraries == (sdk / "lib" / "sample.lib",)
    assert dependency.runtime_files == (sdk / "bin" / "sample.dll",)
    project = api.project("sample")
    assert project.configuration_inputs == (descriptor,)
    assert project.configuration_environment == ("SAMPLE_SDK_ROOT",)


def test_local_sdk_can_declare_an_explicit_materialization_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk = tmp_path / "source-sdk"
    (sdk / "include").mkdir(parents=True)
    descriptor = tmp_path / "sample.sdk.json"
    descriptor.write_text('{"include_dirs":["include"],"materialize":{"required":["include"]}}', encoding="utf-8")
    monkeypatch.setenv("SAMPLE_ROOT", str(sdk))
    api = api_for(tmp_path)

    api.local_sdk(
        "sample",
        descriptor=descriptor.name,
        environment=("SAMPLE_ROOT",),
        materialize_to="vendor/sample",
    )

    spec = api.project("sample").local_sdks[0]
    assert spec.source == sdk
    assert spec.destination == tmp_path / "vendor/sample"
    assert spec.descriptor == descriptor


def test_program_and_file_discovery_record_selected_inputs(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    program = tools / "compiler.exe"
    companion = tools / "runtime.dll"
    program.write_bytes(b"program")
    companion.write_bytes(b"runtime")
    api = api_for(tmp_path)

    assert api.find_program("compiler.exe", roots=(tools,)) == program
    assert api.find_file("runtime.dll", roots=(tools,)) == companion

    project = api.project("sample")
    assert program in project.configuration_inputs
    assert companion in project.configuration_inputs
    assert "PATH" in project.configuration_environment


def test_native_profiles_are_merged_before_target_specific_settings(tmp_path: Path) -> None:
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }", encoding="utf-8")
    api = api_for(tmp_path)
    dependency = api.dependency("platform", defines=("PLATFORM",))
    profile = api.native_profile(
        "common",
        include_dirs=("include",),
        defines=("COMMON",),
        compile_arguments=("-Wall",),
        link_arguments=("-pthread",),
        dependencies=(dependency,),
    )

    target = api.executable(
        "sample", sources=api.files("main.cpp"), profiles=(profile,), defines=("TARGET",)
    )
    spec = api.project("sample", defaults=(target,)).targets[0]

    assert spec.include_dirs == (Path("include"),)
    assert spec.defines == ("COMMON", "TARGET")
    assert spec.compile_arguments == ("-Wall",)
    assert spec.link_arguments == ("-pthread",)
    assert spec.dependencies == (dependency,)


def test_python_requirements_are_registered_as_configuration_inputs(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("sample==1.0\n", encoding="utf-8")
    api = api_for(tmp_path)

    api.python_requirements("requirements.txt")
    project = api.project("sample")

    assert project.python_requirements == (requirements,)
    assert requirements in project.configuration_inputs
