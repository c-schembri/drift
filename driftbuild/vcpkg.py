"""Import vcpkg ports through a pinned standalone client and manifest."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from driftbuild.bootstrap import vcpkg_resolve
from driftbuild.errors import ConfigurationError
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
from driftbuild.storage import drift_home


def _triplet(config: BuildConfig, options: dict[str, str]) -> str:
    architecture = {"x86_64": "x64", "x86": "x86", "arm64": "arm64"}.get(config.architecture)
    if architecture is None:
        raise ConfigurationError(f"vcpkg does not support Drift architecture {config.architecture}")
    system = "windows" if config.platform == "win32" else "osx" if config.platform == "darwin" else "linux"
    suffix = "-static" if system == "windows" and options.get("shared", "false") == "false" else ""
    return f"{architecture}-{system}{suffix}"


def _environment(build_root: Path) -> dict[str, str]:
    environment = dict(os.environ)
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
    manifest_root = _manifest_write(build_root, source, features)
    triplet = _triplet(config, dict(package.options))
    install_root = build_root / "installed"
    installed = install_root / triplet
    stamp = installed / ".drift-installed"
    executable = vcpkg_resolve(state_root.parent)
    environment = _environment(build_root)
    if not stamp.is_file():
        if offline:
            raise ConfigurationError(f"vcpkg package {package.name} is not available in the offline cache")
        _install(executable, manifest_root, install_root, triplet, environment)
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    libraries, runtime_files = _files(installed / ("debug/lib" if config.build_type == "debug" else "lib"))
    extra_runtime = _files(installed / ("debug/bin" if config.build_type == "debug" else "bin"))[1]
    runtime_files = tuple(dict.fromkeys((*runtime_files, *extra_runtime)))
    include = installed / "include"
    dependency = Dependency(
        package.name,
        CompileInterface((include,)),
        LinkInterface(libraries),
        runtime_files,
    )
    action = ActionSpec(
        command=(
            sys.executable,
            "-m",
            "driftbuild.vcpkg",
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
    arguments = parser.parse_args()
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
