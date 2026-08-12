"""Import Conan package recipes and their exported C/C++ interfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, cast

from driftbuild.bootstrap import cmake_resolve, conan_resolve, meson_resolve, ninja_resolve
from driftbuild.errors import ConfigurationError
from driftbuild.model import (
    ActionSpec,
    BuildConfig,
    CompileInterface,
    Dependency,
    LinkInterface,
    ProjectSpec,
    TargetRef,
    TargetSpec,
)
from driftbuild.process import run
from driftbuild.storage import drift_home
from driftbuild.toolchain import toolchain_resolve

_SCHEMA_VERSION = 1


def _state_key(config: BuildConfig) -> str:
    values = (config.platform, config.architecture, config.compiler, config.build_type)
    return "-".join(re.sub(r"[^A-Za-z0-9_.-]", "_", value) for value in values)


def _environment(state_root: Path, config: BuildConfig) -> tuple[dict[str, str], Path]:
    tool_root = state_root.parent
    conan = conan_resolve(tool_root)
    cmake = cmake_resolve(tool_root)
    meson = meson_resolve(tool_root)
    ninja = ninja_resolve(tool_root)
    toolchain = toolchain_resolve(config, tool_root)
    environment = dict(toolchain.environment)
    environment.update({"CC": toolchain.cc, "CXX": toolchain.cxx, "AR": toolchain.archiver})
    environment["CONAN_HOME"] = str(drift_home() / "conan" / _state_key(config))
    environment["PATH"] = os.pathsep.join(
        (str(cmake.parent), str(meson.parent), str(ninja.parent), environment.get("PATH", ""))
    )
    return environment, conan


def _profile_ensure(conan: Path, environment: dict[str, str]) -> None:
    profile = Path(environment["CONAN_HOME"]) / "profiles" / "default"
    if not profile.is_file():
        run((str(conan), "profile", "detect", "--force"), environment=environment, capture=True)


def _create_arguments(
    conan: Path,
    source_root: Path,
    response: Path,
    config: BuildConfig,
    offline: bool,
) -> tuple[str, ...]:
    arguments = [
        str(conan),
        "create",
        str(source_root),
        "--build=missing",
        "--test-folder=",
        "--format=json",
        f"--out-file={response}",
        "-s:h",
        f"build_type={'Debug' if config.build_type == 'debug' else 'Release'}",
    ]
    if offline:
        arguments.append("--no-remote")
    return tuple(arguments)


def _create(
    source_root: Path,
    build_root: Path,
    config: BuildConfig,
    conan: Path,
    environment: dict[str, str],
    offline: bool,
) -> Path:
    response = build_root / "graph.json"
    fingerprint = {
        "schema": _SCHEMA_VERSION,
        "source": str(source_root.resolve()),
        "conan": str(conan.resolve()),
        "conan_mtime_ns": conan.stat().st_mtime_ns,
        "configuration": config.build_type,
        "architecture": config.architecture,
    }
    state_path = build_root / ".drift-import.json"
    if state_path.is_file() and response.is_file():
        try:
            cached = json.loads(state_path.read_text(encoding="utf-8")) == fingerprint
            package_root = _package_node(_json_read(response)).get("package_folder")
            if cached and isinstance(package_root, str) and Path(package_root).is_dir():
                return response
        except (OSError, UnicodeError, json.JSONDecodeError, ConfigurationError):
            pass
    build_root.mkdir(parents=True, exist_ok=True)
    _profile_ensure(conan, environment)
    run(
        _create_arguments(conan, source_root, response, config, offline),
        cwd=source_root,
        environment=environment,
        capture=True,
        timeout_seconds=1800,
    )
    temporary = state_path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(fingerprint, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, state_path)
    return response


def _json_read(path: Path) -> dict[str, Any]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Cannot read Conan graph response {path}: {error}") from error
    if not isinstance(value, dict):
        raise ConfigurationError("Conan graph response is not an object")
    return cast(dict[str, Any], value)


def _package_node(payload: dict[str, Any]) -> dict[str, Any]:
    graph = payload.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, dict):
        raise ConfigurationError("Conan graph response has no nodes")
    root = nodes.get("0")
    if isinstance(root, dict) and isinstance(root.get("package_folder"), str):
        return cast(dict[str, Any], root)
    candidates = [value for value in nodes.values() if isinstance(value, dict) and value.get("package_folder")]
    if len(candidates) != 1:
        raise ConfigurationError("Conan recipe did not expose one unambiguous package node")
    return cast(dict[str, Any], candidates[0])


def _strings(value: object) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def _deploy(node: dict[str, Any], deploy_root: Path) -> None:
    package_folder = node.get("package_folder")
    if not isinstance(package_folder, str) or not Path(package_folder).is_dir():
        raise ConfigurationError("Conan package node has no materialized package folder")
    deploy_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(package_folder, deploy_root, dirs_exist_ok=True)


def _interface(
    node: dict[str, Any], config: BuildConfig, package_root: Path | None = None
) -> tuple[Dependency, tuple[Path, ...], tuple[Path, ...]]:
    package_folder = node.get("package_folder")
    cpp_info = node.get("cpp_info")
    if not isinstance(package_folder, str) or not isinstance(cpp_info, dict):
        raise ConfigurationError("Conan package node has no package folder or C/C++ interface")
    original_root = Path(package_folder).resolve()
    root = original_root if package_root is None else package_root.resolve()

    def package_path(value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            return root / path
        if package_root is not None:
            try:
                return root / path.resolve().relative_to(original_root)
            except ValueError:
                pass
        return path.resolve()

    include_dirs: list[Path] = []
    library_dirs: list[Path] = []
    runtime_dirs: list[Path] = []
    defines: list[str] = []
    compile_arguments: list[str] = []
    link_arguments: list[str] = []
    library_names: list[str] = []
    for component in cpp_info.values():
        if not isinstance(component, dict):
            continue
        include_dirs.extend(package_path(value) for value in _strings(component.get("includedirs")))
        library_dirs.extend(package_path(value) for value in _strings(component.get("libdirs")))
        runtime_dirs.extend(package_path(value) for value in _strings(component.get("bindirs")))
        defines.extend(_strings(component.get("defines")))
        compile_arguments.extend(_strings(component.get("cflags")))
        compile_arguments.extend(_strings(component.get("cxxflags")))
        link_arguments.extend(_strings(component.get("sharedlinkflags")))
        link_arguments.extend(_strings(component.get("exelinkflags")))
        library_names.extend(_strings(component.get("libs")))
        system_libraries = _strings(component.get("system_libs"))
        link_arguments.extend(
            f"{library}.lib" if config.platform == "win32" else f"-l{library}" for library in system_libraries
        )
        for framework in _strings(component.get("frameworks")):
            link_arguments.extend(("-framework", framework))
    outputs: list[Path] = []
    library_suffixes = {".lib", ".a", ".so", ".dylib", ".dll"}
    for directory in (*library_dirs, *runtime_dirs):
        if directory.is_dir():
            outputs.extend(
                path
                for path in sorted(directory.iterdir())
                if path.is_file() and (path.suffix in library_suffixes or ".so." in path.name)
            )
    if library_names:
        selected: list[Path] = []
        for library_name in library_names:
            key = re.sub(r"^lib", "", library_name.casefold())
            match = next(
                (
                    path
                    for path in outputs
                    if re.sub(r"^lib", "", path.stem.casefold()).startswith(key)
                    and path.suffix != ".dll"
                ),
                None,
            )
            if match is not None:
                selected.append(match)
        selected.extend(path for path in outputs if path.suffix == ".dll" and path not in selected)
        outputs = selected or outputs
    runtime_files = tuple(
        path for path in outputs if path.suffix in (".dll", ".so", ".dylib") or ".so." in path.name
    )
    dependency = Dependency(
        str(node.get("ref") or node.get("name") or "conan"),
        CompileInterface(tuple(dict.fromkeys(include_dirs)), tuple(dict.fromkeys(defines)), tuple(compile_arguments)),
        LinkInterface(arguments=tuple(link_arguments)),
        runtime_files,
    )
    return dependency, tuple(dict.fromkeys(outputs)), runtime_files


def project_import(
    source_root: Path,
    state_root: Path,
    config: BuildConfig,
    package_name: str,
    *,
    offline: bool = False,
) -> ProjectSpec:
    """Create a Conan recipe once and import its packaged C/C++ interface."""
    environment, conan = _environment(state_root, config)
    source_key = hashlib.sha256(str(source_root.resolve()).encode("utf-8")).hexdigest()[:16]
    build_root = state_root / package_name / _state_key(config) / "conan" / source_key
    response = _create(source_root, build_root, config, conan, environment, offline)
    node = _package_node(_json_read(response))
    deploy_root = build_root / "package"
    _deploy(node, deploy_root)
    interface, outputs, runtime_files = _interface(node, config, deploy_root)
    if not outputs:
        stamp = deploy_root / ".drift-installed"
        stamp.touch()
        outputs = (stamp,)
    action = ActionSpec(
        command=(
            sys.executable,
            "-m",
            "driftbuild.conan",
            "--source-root",
            str(source_root),
            "--response",
            str(response),
            "--deploy-root",
            str(deploy_root),
            "--conan",
            str(conan),
            "--build-type",
            config.build_type,
            *(("--offline",) if offline else ()),
        ),
        outputs=outputs,
        environment=environment,
        description=f"CONAN {package_name}",
        pool="console",
        restat=True,
    )
    target = TargetSpec(
        package_name,
        "external_library",
        include_dirs=interface.compile.include_dirs,
        defines=interface.compile.defines,
        compile_arguments=interface.compile.arguments,
        dependencies=(interface,),
        runtime_files=runtime_files,
        outputs=outputs,
        action=action,
    )
    return ProjectSpec(package_name, (target,), (TargetRef(package_name),))


def main() -> int:
    """Recreate and deploy one Conan package for a Ninja action."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--deploy-root", type=Path, required=True)
    parser.add_argument("--conan", type=Path, required=True)
    parser.add_argument("--build-type", choices=("debug", "release"), required=True)
    parser.add_argument("--offline", action="store_true")
    arguments = parser.parse_args()
    config = BuildConfig(sys.platform, build_type=arguments.build_type)
    run(
        _create_arguments(arguments.conan, arguments.source_root, arguments.response, config, arguments.offline),
        cwd=arguments.source_root,
        environment=os.environ,
        timeout_seconds=1800,
    )
    _deploy(_package_node(_json_read(arguments.response)), arguments.deploy_root)
    (arguments.deploy_root / ".drift-installed").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
