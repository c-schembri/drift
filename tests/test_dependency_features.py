import hashlib
import io
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from driftbuild.api import BuildConfig, ProjectApi
from driftbuild.bootstrap import vcpkg_resolve
from driftbuild.importers import adapter_detect
from driftbuild.inspection import packages_inspect
from driftbuild.ninja import generate
from driftbuild.package_cache import package_build_root
from driftbuild.packages import package_lock_create, packages_fetch
from driftbuild.prebuilt import project_import as prebuilt_import
from driftbuild.toolchain import toolchain_resolve


def _archive(path: Path, files: dict[str, str]) -> str:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_package_declaration_normalizes_options_features_patches_and_vcpkg(tmp_path: Path) -> None:
    (tmp_path / "fix.patch").write_text("", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("linux"))
    source = api.vcpkg_source("zlib", "1" * 40, features=("tools",))
    api.package(
        "zlib",
        source=source,
        options={"shared": False, "level": 3},
        features=("optimised", "optimised"),
        patches=("fix.patch",),
        adapter="vcpkg",
    )

    package = api.project("sample").packages[0]
    assert package.options == (("level", "3"), ("shared", "false"))
    assert package.features == ("optimised",)
    assert package.patches == (Path("fix.patch"),)
    assert source.features == ("tools",)


@pytest.mark.parametrize(
    ("marker", "adapter"),
    (("Jamroot", "b2"), ("SConstruct", "scons"), ("Makefile", "make")),
)
def test_fallback_adapter_detection_is_manifest_driven(tmp_path: Path, marker: str, adapter: str) -> None:
    (tmp_path / marker).write_text("", encoding="utf-8")
    api = ProjectApi(tmp_path, BuildConfig("linux"))
    package = api.package("sample", source=api.git(str(tmp_path), "1" * 40))

    assert adapter_detect(tmp_path, api.project("sample").packages[0], "linux") == adapter
    assert package.name == "sample"


def test_prebuilt_import_supports_header_only_and_binary_layouts(tmp_path: Path) -> None:
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "sample.h").write_text("int sample(void);\n", encoding="utf-8")
    (tmp_path / "lib").mkdir()
    library = tmp_path / "lib" / "libsample.a"
    library.write_bytes(b"archive")
    api = ProjectApi(tmp_path, BuildConfig("linux"))
    api.package("sample", source=api.git(str(tmp_path), "1" * 40), adapter="prebuilt")
    package = api.project("sample").packages[0]

    project = prebuilt_import(tmp_path, api.config, package)
    dependency = project.targets[0].dependencies[0]

    assert dependency.compile.include_dirs == ((tmp_path / "include").resolve(),)  # type: ignore[union-attr]
    assert dependency.link.libraries == (library.resolve(),)  # type: ignore[union-attr]


def test_patch_changes_locked_source_and_is_recorded_in_provenance(tmp_path: Path) -> None:
    archive = tmp_path / "sample.zip"
    checksum = _archive(archive, {"sample/value.txt": "before\n", "sample/include/value.h": ""})
    (tmp_path / "value.patch").write_text(
        "diff --git a/value.txt b/value.txt\n--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    api = ProjectApi(tmp_path, BuildConfig(sys.platform))
    api.package(
        "sample",
        source=api.archive(str(archive), checksum, strip_prefix="sample"),
        patches=("value.patch",),
        adapter="prebuilt",
    )
    project = api.project("sample")
    store = tmp_path / "store"

    lock = package_lock_create(project, tmp_path, store)
    root = packages_fetch(project, tmp_path, store_root=store, offline=True)["sample"]

    assert (root / "value.txt").read_text(encoding="utf-8") == "after\n"
    assert lock.packages[0].provenance["patches"] == ["value.patch"]


def test_binary_cache_identity_includes_options_and_cross_configuration(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DRIFT_HOME", str(tmp_path / "home"))
    api = ProjectApi(tmp_path, BuildConfig("linux"))
    source = api.git(str(tmp_path), "1" * 40)
    api.package("sample", source=source, options={"shared": False})
    first = api.project("sample").packages[0]
    api = ProjectApi(tmp_path, BuildConfig("linux"))
    api.package("sample", source=source, options={"shared": True})
    second = api.project("sample").packages[0]

    native = package_build_root(tmp_path, first, BuildConfig("linux"), "cmake")
    changed_option = package_build_root(tmp_path, second, BuildConfig("linux"), "cmake")
    cross = package_build_root(tmp_path, first, BuildConfig("linux", target="aarch64-linux-gnu"), "cmake")

    assert len({native, changed_option, cross}) == 3


def test_cross_flags_and_json_toolchain_are_lowered(tmp_path: Path) -> None:
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    sysroot = tmp_path / "sysroot"
    sysroot.mkdir()
    toolchain_file = tmp_path / "toolchain.json"
    toolchain_file.write_text(
        json.dumps(
            {
                "family": "clang",
                "cc": "custom-clang",
                "cxx": "custom-clang++",
                "linker": "custom-clang++",
                "archiver": "custom-ar",
            }
        ),
        encoding="utf-8",
    )
    config = BuildConfig(
        "linux", compiler="clang", target="aarch64-linux-gnu", sysroot=sysroot, toolchain_file=toolchain_file
    )
    api = ProjectApi(tmp_path, config)
    app = api.executable("app", sources=api.files("main.c"))
    project = api.project("sample", defaults=(app,))
    toolchain = toolchain_resolve(config)

    generated = generate(project, tmp_path, tmp_path / "build", config, toolchain)
    ninja = generated.ninja_file.read_text(encoding="utf-8")

    assert "custom-clang" in ninja
    assert "--target=aarch64-linux-gnu" in ninja
    assert f"--sysroot={sysroot}" in ninja


def test_managed_vcpkg_binary_is_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"vcpkg-test"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setenv("DRIFT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("driftbuild.bootstrap._host_key", lambda: ("linux", "x86_64"))
    monkeypatch.setattr("driftbuild.bootstrap._VCPKG_BINARIES", {("linux", "x86_64"): ("vcpkg", digest)})
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))
    install = tmp_path / "home" / "tools" / "vcpkg" / "2026-07-27"
    install.mkdir(parents=True)
    (install / ".vcpkg-root").touch()

    executable = vcpkg_resolve(tmp_path)

    assert executable.read_bytes() == payload


def test_inspect_reports_adapter_cache_lock_and_outputs(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DRIFT_HOME", str(tmp_path / "home"))
    archive = tmp_path / "headers.zip"
    checksum = _archive(archive, {"headers/include/sample.h": "int sample(void);\n"})
    api = ProjectApi(tmp_path, BuildConfig(sys.platform))
    api.package(
        "headers",
        source=api.archive(str(archive), checksum, strip_prefix="headers"),
        adapter="prebuilt",
    )
    project = api.project("sample")
    package_lock_create(project, tmp_path)

    payload = packages_inspect(project, tmp_path, api.config, offline=True)
    package = payload["packages"][0]

    assert package["adapter"] == "prebuilt"
    assert package["binary_cache"]
    assert package["lock"]["content_sha256"]
    assert package["targets"][0]["outputs"][0]["materialized"] is False


def test_git_submodules_are_materialized_when_enabled(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is unavailable")

    def commit(repository: Path, message: str) -> str:
        subprocess.run((git, "add", "."), cwd=repository, check=True)
        subprocess.run(
            (git, "-c", "user.name=Drift", "-c", "user.email=drift@example.invalid", "commit", "-m", message),
            cwd=repository,
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            (git, "rev-parse", "HEAD"), cwd=repository, check=True, capture_output=True, text=True
        ).stdout.strip()

    child = tmp_path / "child"
    child.mkdir()
    subprocess.run((git, "init", "--quiet"), cwd=child, check=True)
    (child / "value.txt").write_text("42\n", encoding="utf-8")
    commit(child, "child")
    parent = tmp_path / "parent"
    parent.mkdir()
    subprocess.run((git, "init", "--quiet"), cwd=parent, check=True)
    subprocess.run(
        (git, "-c", "protocol.file.allow=always", "submodule", "add", str(child), "deps/child"),
        cwd=parent,
        check=True,
        capture_output=True,
    )
    revision = commit(parent, "parent")
    project_root = tmp_path / "project"
    project_root.mkdir()
    api = ProjectApi(project_root, BuildConfig(sys.platform))
    api.package("parent", source=api.git(str(parent), revision, submodules=True))
    project = api.project("sample")

    lock = package_lock_create(project, project_root, tmp_path / "store")
    cached = tmp_path / "store" / "sources" / lock.packages[0].content_sha256

    assert (cached / "deps" / "child" / "value.txt").read_text(encoding="utf-8") == "42\n"
    assert not any(cached.rglob(".git"))
