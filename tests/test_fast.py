import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from driftbuild import __version__
from driftbuild.configuration import config_key
from driftbuild.fast import (
    _architecture,
    _cached_build,
    _cached_run,
    _ninja_build_arguments,
    _operation_find,
    _state_load,
    main,
)
from driftbuild.model import BuildConfig
from driftbuild.versions import NINJA_VERSION


def test_operation_find_ignores_global_option_values() -> None:
    assert _operation_find(["--root", "build", "--compiler=msvc", "run"]) == ("run", 3)


def test_operation_find_does_not_treat_program_argument_as_build() -> None:
    assert _operation_find(["run", "--", "build"]) == ("run", 0)


def test_cached_build_arguments_translate_build_options() -> None:
    assert _ninja_build_arguments(["--jobs=4", "--explain", "app"], True) == (
        ["-f", "build.ninja", "-v", "-j", "4", "-d", "explain", "app"],
        False,
    )
    assert _ninja_build_arguments(["-j", "0"], False) is None


def test_cached_build_result_avoids_loading_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("driftbuild.fast._cached_build", lambda _arguments, _started: 0)

    assert main(["build"]) == 0


@pytest.mark.parametrize(
    ("arguments", "values"),
    ((["build"], {}), (["-D", "flavor=retail", "build"], {"flavor": "retail"})),
)
def test_cached_build_uses_shared_ninja_provider_values_and_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    values: dict[str, str],
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "drift.toml").write_text("[project]\n", encoding="utf-8")
    config = BuildConfig(sys.platform, _architecture(), values=values)
    build_root = root / ".drift" / "build" / config_key(config)
    build_root.mkdir(parents=True)
    (build_root / "build.ninja").touch()
    configured = {
        "drift_version": __version__,
        "inputs": {str(root / "drift.toml"): (root / "drift.toml").stat().st_mtime_ns},
        "directories": {str(root): root.stat().st_mtime_ns},
        "environment": {"DRIFT_TEST_CACHED": "configured"},
        "environment_removed": [],
        "output_phases": {},
        "configuration_environment": {},
    }
    (build_root / "configured.json").write_text(json.dumps(configured), encoding="utf-8")
    cache = tmp_path / "cache"
    ninja = cache / "tools" / "ninja" / NINJA_VERSION / ("ninja.exe" if os.name == "nt" else "ninja")
    ninja.parent.mkdir(parents=True)
    ninja.touch()
    observed: dict[str, object] = {}

    def run_fake(command, **kwargs):
        observed["command"] = command
        observed["cwd"] = kwargs["cwd"]
        observed["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.chdir(root)
    monkeypatch.setenv("DRIFT_HOME", str(cache))
    monkeypatch.setattr("driftbuild.fast.subprocess.run", run_fake)

    assert _cached_build(arguments, 0.0) == 0
    assert observed["command"] == [str(ninja), "-f", "build.ninja"]
    assert observed["cwd"] == build_root
    assert observed["environment"]["DRIFT_TEST_CACHED"] == "configured"  # type: ignore[index]


def test_cached_run_builds_declared_target_and_launches_without_loading_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    manifest = root / "drift.toml"
    manifest.write_text("[project]\n", encoding="utf-8")
    config = BuildConfig(sys.platform, _architecture())
    build_root = root / ".drift" / "build" / config_key(config)
    build_root.mkdir(parents=True)
    (build_root / "build.ninja").touch()
    executable = root / "build" / "sample.exe"
    configured = {
        "drift_version": __version__,
        "inputs": {str(manifest): manifest.stat().st_mtime_ns},
        "directories": {str(root): root.stat().st_mtime_ns},
        "environment": {},
        "environment_removed": [],
        "output_phases": {},
        "configuration_environment": {},
        "run_commands": [
            {
                "path": ["client"],
                "target": "sample",
                "build_targets": ["sample", "editor"],
                "command": [str(executable)],
                "working_directory": str(executable.parent),
                "environment": {"SAMPLE": "configured"},
                "runtime_directories": [str(executable.parent)],
                "executable": str(executable),
            }
        ],
    }
    (build_root / "configured.json").write_text(json.dumps(configured), encoding="utf-8")
    cache = tmp_path / "cache"
    ninja = cache / "tools" / "ninja" / NINJA_VERSION / ("ninja.exe" if os.name == "nt" else "ninja")
    ninja.parent.mkdir(parents=True)
    ninja.touch()
    observed: dict[str, object] = {}

    def run_fake(command, **kwargs):
        observed["ninja"] = command
        return subprocess.CompletedProcess(command, 0)

    def launch_fake(spec, arguments):  # type: ignore[no-untyped-def]
        observed["spec"] = spec
        observed["arguments"] = arguments
        return 7

    monkeypatch.chdir(root)
    monkeypatch.setenv("DRIFT_HOME", str(cache))
    monkeypatch.setattr("driftbuild.fast.subprocess.run", run_fake)
    monkeypatch.setattr("driftbuild.runner.launch", launch_fake)

    assert _cached_run(["run", "client", "--", "--sample"], 0.0) == 7
    assert observed["ninja"] == [str(ninja), "-f", "build.ninja", "sample", "editor"]
    assert observed["arguments"] == ["--sample"]
    assert observed["spec"].target == "sample"  # type: ignore[union-attr]


def test_cached_state_invalidates_when_discovery_directory_changes(tmp_path: Path) -> None:
    provider = tmp_path / "drift_project.py"
    provider.write_text("", encoding="utf-8")
    sources = tmp_path / "src"
    sources.mkdir()
    state_path = tmp_path / "configured.json"
    state = {
        "drift_version": __version__,
        "inputs": {str(provider): provider.stat().st_mtime_ns},
        "directories": {str(sources): sources.stat().st_mtime_ns},
        "environment": {},
        "environment_removed": [],
        "output_phases": {},
        "configuration_environment": {},
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    assert _state_load(state_path) is not None
    (sources / "new.cpp").write_text("", encoding="utf-8")
    assert _state_load(state_path) is None


def test_cached_state_invalidates_when_configuration_environment_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = tmp_path / "drift_project.py"
    provider.write_text("", encoding="utf-8")
    state_path = tmp_path / "configured.json"
    state = {
        "drift_version": __version__,
        "inputs": {str(provider): provider.stat().st_mtime_ns},
        "directories": {},
        "environment": {},
        "environment_removed": [],
        "output_phases": {},
        "configuration_environment": {"SAMPLE_SDK_ROOT": "one"},
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv("SAMPLE_SDK_ROOT", "one")

    assert _state_load(state_path) is not None

    monkeypatch.setenv("SAMPLE_SDK_ROOT", "two")
    assert _state_load(state_path) is None
