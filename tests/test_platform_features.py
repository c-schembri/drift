import json
import subprocess
import sys
from pathlib import Path

from driftbuild.api import BuildConfig, ProjectApi
from driftbuild.cache import cache_export, cache_import, cache_pull, cache_push
from driftbuild.distribution import standalone_create
from driftbuild.install import project_install
from driftbuild.ninja import generate
from driftbuild.toolchain import Toolchain, toolchain_resolve
from driftbuild.vscode import generate as vscode_generate
from driftbuild.xcode import generate as xcode_generate


def _gcc() -> Toolchain:
    return Toolchain("gcc", "gcc", "g++", "g++", "ar", {}, ".o", "", "lib", ".a", "lib", ".so")


def test_native_modes_and_unity_are_lowered_to_ninja(tmp_path: Path) -> None:
    (tmp_path / "pch.h").write_text("#pragma once\n", encoding="utf-8")
    for name in ("one.c", "two.c"):
        (tmp_path / name).write_text(f"int {name[:3]}(void) {{ return 1; }}\n", encoding="utf-8")
    config = BuildConfig(
        "linux",
        compiler="gcc",
        sanitizers=("address",),
        coverage=True,
        lto=True,
        warnings="error",
        unity_size=8,
    )
    api = ProjectApi(tmp_path, config)
    library = api.static_library(
        "sample",
        sources=api.files("one.c", "two.c"),
        precompiled_header="pch.h",
    )
    project = api.project("sample", defaults=(library,))

    result = generate(project, tmp_path, tmp_path / ".drift", config, _gcc())
    ninja = result.ninja_file.read_text(encoding="utf-8")

    assert "unity/sample/0.c" in ninja.replace("$:", ":").replace("\\", "/")
    assert "-fsanitize=address" in ninja
    assert "--coverage" in ninja
    assert "-flto" in ninja
    assert "-Wall" in ninja and "-Werror" in ninja
    assert "-include" in ninja and "pch.h" in ninja


def test_install_writes_sdk_layout_manifest_and_pkg_config(tmp_path: Path) -> None:
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "sample.h").write_text("int sample(void);\n", encoding="utf-8")
    (tmp_path / "sample.c").write_text("int sample(void) { return 1; }\n", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("linux"))
    library = api.static_library(
        "sample",
        sources=api.files("sample.c"),
        public_headers=api.files("include/sample.h"),
    )
    project = api.project("sample", defaults=(library,))
    built = tmp_path / "libsample.a"
    built.write_bytes(b"archive")

    manifest = project_install(project, tmp_path, tmp_path / "sdk", {"sample": (built,)})

    assert (tmp_path / "sdk/lib/libsample.a").read_bytes() == b"archive"
    assert (tmp_path / "sdk/include/sample.h").is_file()
    assert (tmp_path / "sdk/lib/pkgconfig/sample.pc").is_file()
    assert json.loads(manifest.read_text(encoding="utf-8"))["project"] == "sample"


def test_cache_round_trip_and_portable_generators(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    home = tmp_path / "home"
    monkeypatch.setenv("DRIFT_HOME", str(home))
    cached = home / "store/sources/abc/value.txt"
    cached.parent.mkdir(parents=True)
    cached.write_text("cached", encoding="utf-8")
    archive = cache_export(tmp_path / "cache.tar.gz", ("sources",))
    cached.unlink()

    assert cache_import(archive) >= 1
    assert cached.read_text(encoding="utf-8") == "cached"
    remote = tmp_path / "remote-cache.tar.gz"
    cache_push(remote.as_uri(), ("sources",))
    cached.unlink()
    assert cache_pull(remote.as_uri()) >= 1
    assert cached.read_text(encoding="utf-8") == "cached"

    api = ProjectApi(tmp_path, BuildConfig("linux", compiler="gcc"))
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    app = api.executable("app", sources=api.files("main.c"))
    project = api.project("sample", defaults=(app,))
    vscode = vscode_generate(project, tmp_path, api.config)
    xcode = xcode_generate(project, tmp_path)
    assert (vscode / "tasks.json").is_file()
    assert (xcode / "project.pbxproj").is_file()


def test_standalone_zipapp_runs_without_installation(tmp_path: Path) -> None:
    archive = standalone_create(tmp_path / "drift.pyz")

    completed = subprocess.run(
        (sys.executable, str(archive), "--version"),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.startswith("drift ")


def test_hermetic_toolchain_removes_ambient_build_flags(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CFLAGS", "-DHOST_LEAK=1")
    monkeypatch.setenv("PKG_CONFIG_PATH", str(tmp_path))
    monkeypatch.setattr("driftbuild.toolchain.shutil.which", lambda value, **_kwargs: value)

    toolchain = toolchain_resolve(BuildConfig("linux", compiler="gcc", hermetic=True))

    assert "CFLAGS" not in toolchain.environment
    assert "PKG_CONFIG_PATH" not in toolchain.environment
    assert toolchain.environment["SOURCE_DATE_EPOCH"] == "0"
