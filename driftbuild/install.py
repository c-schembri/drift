"""Install native project outputs into a conventional SDK prefix."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.graph import project_validate, transitive_targets
from driftbuild.model import Artifact, ProjectSpec, TargetSpec


def _copy(source: Path, destination: Path, installed: list[dict[str, str]]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    installed.append(
        {
            "path": destination.as_posix(),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        }
    )


def project_install(
    project: ProjectSpec,
    root: Path,
    prefix: Path,
    outputs: dict[str, tuple[Path, ...]],
    names: tuple[str, ...] = (),
) -> Path:
    """Install selected targets and return the generated manifest path."""
    requested_names = set(names or (reference.name for reference in project.defaults))
    if not requested_names:
        requested_names = {
            target.name
            for target in project.targets
            if not target.name.startswith("__drift_package_")
            and target.kind in ("static_library", "shared_library", "executable", "runtime_bundle")
        }
    targets = project_validate(project)
    missing = sorted(requested_names - targets.keys())
    if missing:
        raise ConfigurationError("Unknown install targets: " + ", ".join(missing))
    reachable = set(transitive_targets(targets, sorted(requested_names)))
    selected: list[TargetSpec] = [
        target
        for target in project.targets
        if target.name in reachable
        and not target.name.startswith("__drift_package_")
        and target.kind in ("static_library", "shared_library", "executable", "runtime_bundle")
    ]
    selected_names = {target.name for target in selected}
    installed: list[dict[str, str]] = []
    for target in selected:
        destination_root = prefix / ("bin" if target.kind == "executable" else "lib")
        for output in outputs.get(target.name, ()):
            if output.is_file() and not output.name.startswith("."):
                _copy(output, destination_root / output.name, installed)
        for header in target.public_headers:
            if isinstance(header, Artifact):
                continue
            source = header if header.is_absolute() else root / header
            if source.is_file():
                relative_path = Path(header.name) if header.is_absolute() else header
                if relative_path.parts[:1] == ("include",):
                    relative_path = Path(*relative_path.parts[1:])
                relative = relative_path.as_posix()
                _copy(source, prefix / "include" / relative, installed)
        for runtime in target.runtime_files:
            if isinstance(runtime, Artifact):
                continue
            source = runtime if runtime.is_absolute() else root / runtime
            if source.is_file():
                _copy(source, prefix / "bin" / source.name, installed)
        if target.kind in ("static_library", "shared_library"):
            pc = prefix / "lib" / "pkgconfig" / f"{target.name}.pc"
            pc.parent.mkdir(parents=True, exist_ok=True)
            library_outputs = [path for path in outputs.get(target.name, ()) if path.suffix.casefold() != ".dll"]
            library_argument = (
                library_outputs[0].name
                if library_outputs and library_outputs[0].suffix.casefold() == ".lib"
                else f"-l{target.name.removeprefix('lib')}"
            )
            pc.write_text(
                "prefix=${pcfiledir}/../..\n"
                "libdir=${prefix}/lib\n"
                "includedir=${prefix}/include\n\n"
                f"Name: {target.name}\nDescription: Installed by Drift\nVersion: 0\n"
                f"Libs: -L${{libdir}} {library_argument}\nCflags: -I${{includedir}}\n",
                encoding="utf-8",
            )
            installed.append({"path": pc.as_posix(), "sha256": hashlib.sha256(pc.read_bytes()).hexdigest()})
    manifest = prefix / "drift-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    relative_files = [
        {"path": Path(item["path"]).relative_to(prefix).as_posix(), "sha256": item["sha256"]} for item in installed
    ]
    manifest.write_text(
        json.dumps({"project": project.name, "targets": sorted(selected_names), "files": relative_files}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return manifest
