"""Import vcpkg ports through a pinned standalone client and manifest."""

from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path

from driftbuild.bootstrap import vcpkg_resolve
from driftbuild.errors import ConfigurationError
from driftbuild.locking import cache_lock
from driftbuild.model import (
    ActionSpec,
    BuildConfig,
    CompileInterface,
    Dependency,
    LinkInterface,
    PackageSpec,
    ProjectSpec,
    TargetRef,
    TargetSpec,
    VcpkgSource,
)
from driftbuild.package_cache import package_build_root
from driftbuild.process import run
from driftbuild.runtime import module_command
from driftbuild.storage import drift_home
from driftbuild.toolchain import toolchain_resolve


def _triplet(config: BuildConfig, package: PackageSpec) -> str:
    architecture = {"x86_64": "x64", "x86": "x86", "arm64": "arm64"}.get(config.architecture)
    if architecture is None:
        raise ConfigurationError(f"vcpkg does not support Drift architecture {config.architecture}")
    system = "windows" if config.platform == "win32" else "osx" if config.platform == "darwin" else "linux"
    options = dict(package.options)
    shared = package.linkage == "shared" or options.get("shared", "false") == "true"
    suffix = "-static-md" if system == "windows" and not shared else ""
    return f"{architecture}-{system}{suffix}"


def _environment(build_root: Path, state_root: Path, config: BuildConfig) -> dict[str, str]:
    environment = dict(toolchain_resolve(config, state_root.parent).environment)
    binary_cache = drift_home() / "vcpkg" / "archives"
    downloads = drift_home() / "vcpkg" / "downloads"
    binary_cache.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(parents=True, exist_ok=True)
    environment["VCPKG_DEFAULT_BINARY_CACHE"] = str(binary_cache)
    environment["VCPKG_DOWNLOADS"] = str(downloads)
    environment["VCPKG_ROOT"] = str(vcpkg_resolve(build_root).parent)
    return environment


def _manifest_write(build_root: Path, source: VcpkgSource, features: tuple[str, ...]) -> Path:
    manifest_root = build_root / "manifest"
    manifest_root.mkdir(parents=True, exist_ok=True)
    dependency: dict[str, object] = {"name": source.port}
    if features:
        dependency["features"] = list(features)
    manifest: dict[str, object] = {"dependencies": [dependency]}
    if source.registry == "https://github.com/microsoft/vcpkg":
        manifest["builtin-baseline"] = source.baseline
    (manifest_root / "vcpkg.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if source.registry != "https://github.com/microsoft/vcpkg":
        configuration = {
            "default-registry": {
                "kind": "git",
                "repository": source.registry,
                "baseline": source.baseline,
            }
        }
        (manifest_root / "vcpkg-configuration.json").write_text(
            json.dumps(configuration, indent=2) + "\n", encoding="utf-8"
        )
    return manifest_root


def _install(
    executable: Path,
    manifest_root: Path,
    install_root: Path,
    triplet: str,
    environment: dict[str, str],
) -> None:
    run(
        (
            str(executable),
            "install",
            f"--x-manifest-root={manifest_root}",
            f"--x-install-root={install_root}",
            f"--x-buildtrees-root={install_root.parent / 'buildtrees'}",
            f"--x-packages-root={install_root.parent / 'packages'}",
            f"--triplet={triplet}",
            "--clean-after-build",
        ),
        environment=environment,
        timeout_seconds=3600,
    )


def _files(root: Path) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    link_suffixes = {".a", ".lib", ".so", ".dylib"}
    libraries = tuple(
        path.resolve()
        for path in sorted(root.rglob("*"))
        if path.is_file() and (path.suffix.casefold() in link_suffixes or ".so." in path.name)
    )
    runtime = tuple(
        path.resolve()
        for path in sorted(root.rglob("*"))
        if path.is_file() and (path.suffix.casefold() in (".dll", ".dylib") or ".so." in path.name)
    )
    return libraries, runtime


def _owned_files(installed: Path, port: str, build_type: str) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...]]:
    info = installed.parent / "vcpkg" / "info"
    lists = sorted(info.glob(f"{port}_*.list")) if info.is_dir() else []
    paths: list[Path] = []
    for manifest in lists:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            relative = Path(line.strip())
            parts = relative.parts[1:] if relative.parts[:1] == (installed.name,) else relative.parts
            path = installed.joinpath(*parts)
            if path.is_file():
                paths.append(path.resolve())
    if not paths:
        libraries, runtime = _files(installed / ("debug/lib" if build_type == "debug" else "lib"))
        runtime += _files(installed / ("debug/bin" if build_type == "debug" else "bin"))[1]
        return libraries, tuple(dict.fromkeys(runtime)), ()
    debug_prefix = ("debug",) if build_type == "debug" else ()
    library_roots = {debug_prefix + ("lib",), debug_prefix + ("lib64",)}
    binary_roots = {debug_prefix + ("bin",)}
    libraries = tuple(
        path
        for path in paths
        if any(path.relative_to(installed).parts[: len(prefix)] == prefix for prefix in library_roots)
        and (path.suffix.casefold() in (".a", ".lib", ".so", ".dylib") or ".so." in path.name)
    )
    runtime = tuple(
        path
        for path in paths
        if any(
            path.relative_to(installed).parts[: len(prefix)] == prefix
            for prefix in (*library_roots, *binary_roots)
        )
        and (path.suffix.casefold() in (".dll", ".dylib") or ".so." in path.name)
    )
    pc_root = debug_prefix + ("lib", "pkgconfig")
    pc_files = tuple(
        path
        for path in paths
        if path.suffix.casefold() == ".pc"
        and path.relative_to(installed).parts[: len(pc_root)] == pc_root
    )
    return libraries, runtime, pc_files


def _pc_expand(value: str, variables: dict[str, str]) -> str:
    for _index in range(16):
        replaced = value
        for name, item in variables.items():
            replaced = replaced.replace("${" + name + "}", item)
        if replaced == value:
            return replaced
        value = replaced
    return value


def _pc_arguments(
    files: tuple[Path, ...], config: BuildConfig
) -> tuple[tuple[Path, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    include_dirs: list[Path] = []
    defines: list[str] = []
    compile_arguments: list[str] = []
    link_arguments: list[str] = []
    for path in files:
        variables: dict[str, str] = {"pcfiledir": str(path.parent)}
        fields: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and ":" not in line.split("=", 1)[0]:
                name, value = line.split("=", 1)
                variables[name.strip()] = value.strip()
            elif ":" in line:
                name, value = line.split(":", 1)
                fields[name.strip()] = value.strip()
        cflags = shlex.split(_pc_expand(fields.get("Cflags", ""), variables), posix=os.name != "nt")
        for value in cflags:
            value = value.replace('"', "")
            if value.startswith("-I"):
                include_dirs.append(Path(value[2:]).resolve())
            elif value.startswith("-D"):
                defines.append(value[2:])
            elif config.platform != "win32" or not value.startswith("-"):
                compile_arguments.append(value)
        link_fields = fields.get("Libs.private", "") if config.platform == "win32" else " ".join(
            (fields.get("Libs", ""), fields.get("Libs.private", ""))
        )
        for value in shlex.split(_pc_expand(link_fields, variables), posix=os.name != "nt"):
            value = value.replace('"', "")
            if config.platform == "win32":
                if value.startswith("-l"):
                    link_arguments.append(value[2:] + ".lib")
                elif not value.startswith(("-L", "-Wl,", "-pthread")):
                    link_arguments.append(value)
            else:
                link_arguments.append(value)
    return (
        tuple(dict.fromkeys(include_dirs)),
        tuple(dict.fromkeys(defines)),
        tuple(dict.fromkeys(compile_arguments)),
        tuple(dict.fromkeys(link_arguments)),
    )


def project_import(
    source_root: Path,
    state_root: Path,
    config: BuildConfig,
    package: PackageSpec,
    *,
    offline: bool = False,
) -> ProjectSpec:
    """Resolve a pinned vcpkg port and expose its installed C/C++ interface."""
    if not isinstance(package.source, VcpkgSource):
        raise ConfigurationError("vcpkg adapter requires api.vcpkg_source()")
    build_root = package_build_root(source_root, package, config, "vcpkg")
    source = package.source
    features = tuple(dict.fromkeys((*source.features, *package.features)))
    triplet = _triplet(config, package)
    install_root = build_root / "installed"
    installed = install_root / triplet
    stamp = installed / ".drift-installed"
    executable = vcpkg_resolve(state_root.parent)
    environment = _environment(build_root, state_root, config)
    with cache_lock(build_root.with_suffix(".lock")):
        manifest_root = _manifest_write(build_root, source, features)
        if not stamp.is_file():
            if offline:
                raise ConfigurationError(f"vcpkg package {package.name} is not available in the offline cache")
            _install(executable, manifest_root, install_root, triplet, environment)
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.touch()
    libraries, runtime_files, pc_files = _owned_files(installed, source.port, config.build_type)
    pc_includes, pc_defines, pc_compile, pc_link = _pc_arguments(pc_files, config)
    include = installed / "include"
    dependency = Dependency(
        package.name,
        CompileInterface((include, *pc_includes), pc_defines, pc_compile),
        LinkInterface(arguments=pc_link),
        runtime_files,
    )
    action = ActionSpec(
        command=(
            *module_command("driftbuild.vcpkg"),
            "--vcpkg",
            str(executable),
            "--manifest-root",
            str(manifest_root),
            "--install-root",
            str(install_root),
            "--triplet",
            triplet,
            "--stamp",
            str(stamp),
            "--lock",
            str(build_root.with_suffix(".lock")),
        ),
        outputs=(*libraries, *runtime_files, stamp),
        environment=environment,
        description=f"VCPKG {package.name}",
        pool="console",
        restat=True,
    )
    target = TargetSpec(
        package.name,
        "external_library",
        include_dirs=(include,),
        dependencies=(dependency,),
        runtime_files=runtime_files,
        outputs=action.outputs,
        action=action,
    )
    return ProjectSpec(package.name, (target,), (TargetRef(package.name),))


def main() -> int:
    """Install one manifest and write its completion marker."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--vcpkg", type=Path, required=True)
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--triplet", required=True)
    parser.add_argument("--stamp", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    arguments = parser.parse_args()
    with cache_lock(arguments.lock):
        _install(
            arguments.vcpkg,
            arguments.manifest_root,
            arguments.install_root,
            arguments.triplet,
            dict(os.environ),
        )
        arguments.stamp.parent.mkdir(parents=True, exist_ok=True)
        arguments.stamp.touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
