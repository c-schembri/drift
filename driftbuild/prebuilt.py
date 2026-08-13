"""Import header-only and prebuilt binary package layouts."""

from __future__ import annotations

import argparse
from pathlib import Path

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
)
from driftbuild.package_cache import package_build_root
from driftbuild.runtime import module_command


def _libraries(source_root: Path, config: BuildConfig) -> tuple[Path, ...]:
    suffixes = {".lib", ".a"}
    if config.platform == "win32":
        suffixes.add(".dll")
    elif config.platform == "darwin":
        suffixes.add(".dylib")
    else:
        suffixes.add(".so")
    roots = [path for name in ("lib", "libs", "bin") for path in (source_root / name,) if path.is_dir()]
    return tuple(
        path.resolve()
        for root in roots
        for path in sorted(root.rglob("*"))
        if path.is_file() and (path.suffix.casefold() in suffixes or ".so." in path.name)
    )


def project_import(source_root: Path, config: BuildConfig, package: PackageSpec) -> ProjectSpec:
    """Expose conventional include, library, and runtime directories."""
    include_dirs = tuple(
        path.resolve() for name in ("include", "headers") for path in (source_root / name,) if path.is_dir()
    )
    if not include_dirs and any(source_root.glob("*.h")):
        include_dirs = (source_root.resolve(),)
    libraries = _libraries(source_root, config)
    if not include_dirs and not libraries:
        raise ConfigurationError(f"Prebuilt package {package.name} has no headers or native libraries")
    link_libraries = tuple(path for path in libraries if path.suffix.casefold() not in (".dll", ".so", ".dylib"))
    if not link_libraries:
        link_libraries = tuple(path for path in libraries if path.suffix.casefold() != ".dll")
    runtime_files = tuple(
        path for path in libraries if path.suffix.casefold() in (".dll", ".so", ".dylib") or ".so." in path.name
    )
    dependency = Dependency(
        package.name,
        CompileInterface(include_dirs),
        LinkInterface(link_libraries),
        runtime_files,
    )
    stamp = package_build_root(source_root, package, config, "prebuilt") / ".drift-imported"
    action = ActionSpec(
        command=(*module_command("driftbuild.prebuilt"), "--stamp", str(stamp)),
        outputs=(stamp,),
        description=f"PREBUILT {package.name}",
        restat=True,
    )
    target = TargetSpec(
        package.name,
        "external_library",
        include_dirs=include_dirs,
        dependencies=(dependency,),
        runtime_files=runtime_files,
        outputs=(stamp,),
        action=action,
    )
    return ProjectSpec(package.name, (target,), (TargetRef(package.name),))


def main() -> int:
    """Create the stable marker for an immutable prebuilt interface."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--stamp", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.stamp.parent.mkdir(parents=True, exist_ok=True)
    arguments.stamp.touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
