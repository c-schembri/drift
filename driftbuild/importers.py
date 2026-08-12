"""Detection and dispatch for upstream package build descriptions."""

from __future__ import annotations

from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.model import BuildConfig, MsbuildProject, PackageBuild, ProjectSpec


def project_import(
    source_root: Path,
    state_root: Path,
    config: BuildConfig,
    package_name: str,
    build: PackageBuild | None,
) -> ProjectSpec:
    """Select a build-system adapter and return its normalized target graph."""
    if isinstance(build, MsbuildProject):
        from driftbuild.msbuild import project_import as msbuild_import

        return msbuild_import(source_root, config, build)

    if config.platform == "win32":
        from driftbuild.msbuild import project_discover

        discovered = project_discover(source_root, config, package_name)
        if discovered is not None:
            return discovered

    if (source_root / "CMakeLists.txt").is_file():
        from driftbuild.cmake import project_import as cmake_import

        return cmake_import(source_root, state_root, config, package_name)

    mechanisms: list[str] = []
    if (source_root / "meson.build").is_file():
        mechanisms.append("Meson")
    if (source_root / "conanfile.py").is_file() or (source_root / "conanfile.txt").is_file():
        mechanisms.append("Conan")
    if (source_root / "vcpkg.json").is_file():
        mechanisms.append("vcpkg")
    detail = f" (detected {', '.join(mechanisms)})" if mechanisms else ""
    raise ConfigurationError(
        f"Package {package_name} has no supported build-system adapter{detail}; provide a Drift overlay"
    )
