"""Detection and dispatch for upstream package build descriptions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from driftbuild.bootstrap import CMAKE_VERSION, CONAN_VERSION, MESON_VERSION
from driftbuild.errors import ConfigurationError
from driftbuild.model import BuildConfig, MsbuildProject, PackageSpec, ProjectSpec, VcpkgSource


def adapter_detect(source_root: Path, package: PackageSpec, platform: str) -> str:
    """Return the selected adapter without executing an upstream build tool."""
    if package.adapter is not None:
        return package.adapter
    if isinstance(package.source, VcpkgSource):
        return "vcpkg"
    if isinstance(package.build, MsbuildProject):
        return "msbuild"
    if (source_root / "conanfile.py").is_file():
        return "conan"
    if platform == "win32":
        from driftbuild.msbuild import project_discover

        if project_discover(source_root, BuildConfig(platform), package.name) is not None:
            return "msbuild"
    markers = (
        ("CMakeLists.txt", "cmake"),
        ("meson.build", "meson"),
        ("configure", "autotools"),
        ("Jamroot", "b2"),
        ("Jamfile", "b2"),
        ("SConstruct", "scons"),
        ("Makefile", "make"),
    )
    for marker, adapter in markers:
        if (source_root / marker).is_file():
            return adapter
    if any((source_root / name).is_dir() for name in ("include", "lib", "libs")):
        return "prebuilt"
    mechanisms: list[str] = []
    if (source_root / "configure.ac").is_file():
        mechanisms.append("Autotools without a generated configure script")
    if (source_root / "conanfile.txt").is_file():
        mechanisms.append("a consumer-only Conan manifest")
    detail = f" (detected {', '.join(mechanisms)})" if mechanisms else ""
    raise ConfigurationError(
        f"Package {package.name} has no supported build-system adapter{detail}; "
        "select adapter='prebuilt' or provide a Drift overlay"
    )


def package_provenance(source_root: Path, package: PackageSpec, platform: str) -> dict[str, Any]:
    """Describe the immutable inputs used to choose and configure an adapter."""
    if package.overlay is not None:
        adapter = "overlay"
    elif (source_root / "drift.toml").is_file():
        adapter = "drift"
    else:
        try:
            adapter = adapter_detect(source_root, package, platform)
        except ConfigurationError:
            adapter = "unresolved"
    versions = {
        "cmake": CMAKE_VERSION,
        "meson": MESON_VERSION,
        "conan": CONAN_VERSION,
    }
    return {
        "adapter": adapter,
        "adapter_version": versions.get(adapter, "host"),
        "options": dict(package.options),
        "features": list(package.features),
        "patches": [path.as_posix() for path in package.patches],
    }


def project_import(
    source_root: Path,
    state_root: Path,
    config: BuildConfig,
    package: PackageSpec,
    *,
    offline: bool = False,
) -> ProjectSpec:
    """Select a build-system adapter and return its normalized target graph."""
    adapter = adapter_detect(source_root, package, config.platform)
    if adapter == "msbuild":
        from driftbuild.msbuild import project_discover
        from driftbuild.msbuild import project_import as msbuild_import

        if isinstance(package.build, MsbuildProject):
            return msbuild_import(source_root, config, package.build)
        discovered = project_discover(source_root, config, package.name)
        if discovered is None:
            raise ConfigurationError(f"Package {package.name} has no discoverable Visual C++ project")
        return discovered
    if adapter == "cmake":
        from driftbuild.cmake import project_import as cmake_import

        return cmake_import(source_root, state_root, config, package)
    if adapter == "meson":
        from driftbuild.meson import project_import as meson_import

        return meson_import(source_root, state_root, config, package)
    if adapter == "autotools":
        from driftbuild.autotools import project_import as autotools_import

        return autotools_import(source_root, state_root, config, package)
    if adapter == "conan":
        from driftbuild.conan import project_import as conan_import

        return conan_import(source_root, state_root, config, package, offline=offline)
    if adapter == "vcpkg":
        from driftbuild.vcpkg import project_import as vcpkg_import

        return vcpkg_import(source_root, state_root, config, package, offline=offline)
    if adapter == "prebuilt":
        from driftbuild.prebuilt import project_import as prebuilt_import

        return prebuilt_import(source_root, config, package)
    if adapter in ("make", "b2", "scons"):
        from driftbuild.opaque import project_import as opaque_import

        return opaque_import(source_root, state_root, config, package, adapter)
    raise ConfigurationError(f"Unsupported package adapter: {adapter}")
