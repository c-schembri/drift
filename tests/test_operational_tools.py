from pathlib import Path

import pytest

from driftbuild.build import BuildResult, build
from driftbuild.cache import cache_clean, cache_status, size_render
from driftbuild.doctor import doctor_run
from driftbuild.errors import ExecutionError
from driftbuild.model import BuildConfig, ProjectSpec
from driftbuild.ninja import GeneratedBuild
from driftbuild.process import ProcessResult
from driftbuild.toolchain import Toolchain


def test_cache_status_and_confirmed_cleanup_are_scoped_to_drift_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    source = home / "store" / "sources" / "digest" / "source.c"
    binary = home / "binaries" / "key" / "library.a"
    source.parent.mkdir(parents=True)
    binary.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    binary.write_bytes(b"library")
    monkeypatch.setenv("DRIFT_HOME", str(home))

    status = {entry.name: entry for entry in cache_status()}

    assert status["sources"].bytes == 6
    assert status["binaries"].files == 1
    with pytest.raises(ExecutionError, match="--yes"):
        cache_clean(("binaries",), confirmed=False)
    removed = cache_clean(("binaries",), confirmed=True)
    assert removed[0].bytes == 7
    assert not (home / "binaries").exists()
    assert source.is_file()


def test_size_render_uses_binary_units() -> None:
    assert size_render(42) == "42 B"
    assert size_render(1536) == "1.5 KiB"


def test_doctor_reports_configuration_toolchain_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIFT_HOME", str(tmp_path / "cache"))
    toolchain = Toolchain("gcc", "gcc", "g++", "g++", "ar", {}, ".o", "", "lib", ".a", "lib", ".so")
    monkeypatch.setattr("driftbuild.doctor.toolchain_resolve", lambda _config, _root: toolchain)
    monkeypatch.setattr("driftbuild.doctor.shutil.which", lambda value, **_kwargs: f"/tools/{value}")

    result = doctor_run(ProjectSpec("sample"), tmp_path, BuildConfig("linux", compiler="gcc"))

    assert result["ok"] is True
    assert result["configuration"].startswith("linux-x86_64-gcc-debug")
    checks = {value["name"]: value for value in result["checks"]}
    assert checks["toolchain"]["status"] == "ok"
    assert checks["packages"]["detail"] == "no locked packages declared"
    assert checks["cache"]["detail"] == str((tmp_path / "cache").resolve())


def test_build_forwards_parallel_verbose_explain_and_keep_going_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_root = tmp_path / "build"
    build_root.mkdir()
    ninja_file = build_root / "build.ninja"
    ninja_file.write_text("", encoding="utf-8")
    generated = GeneratedBuild(ninja_file, build_root / "compile_commands.json", {}, {})
    toolchain = Toolchain("gcc", "gcc", "g++", "g++", "ar", {}, ".o", "", "lib", ".a", "lib", ".so")
    monkeypatch.setattr("driftbuild.build.configure", lambda *_args, **_kwargs: BuildResult(generated, toolchain))
    monkeypatch.setattr("driftbuild.build.ninja_resolve", lambda _root: Path("ninja"))
    received: tuple[str, ...] = ()

    def fake_run(arguments, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal received
        received = tuple(str(value) for value in arguments)
        return ProcessResult(received, 0, "", "")

    monkeypatch.setattr("driftbuild.build.run", fake_run)

    build(
        ProjectSpec("sample"),
        tmp_path,
        tmp_path / ".drift",
        BuildConfig("linux"),
        ("app",),
        jobs=8,
        verbose=True,
        explain=True,
        keep_going=True,
        dry_run=True,
    )

    assert received == (
        "ninja",
        "-f",
        "build.ninja",
        "-j",
        "8",
        "-v",
        "-d",
        "explain",
        "-k",
        "0",
        "-n",
        "app",
    )
