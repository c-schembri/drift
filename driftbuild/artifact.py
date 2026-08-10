"""Reproducible artifact assembly."""

from __future__ import annotations

import gzip
import tarfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from driftbuild.errors import ExecutionError
from driftbuild.model import Artifact, ProjectSpec


def _resolve_file(root: Path, value: Path | Artifact, outputs: Mapping[str, tuple[Path, ...]]) -> Path:
    if isinstance(value, Path):
        return root / value
    candidates = outputs.get(value.target.name, ())
    result = next((path for path in candidates if path.name == value.path.name or path == value.path), None)
    if result is None and candidates and value.path == Path(value.target.name):
        result = candidates[0]
    if result is None:
        raise ExecutionError(f"Cannot resolve artifact output {value.target.name}:{value.path}")
    return result


def artifacts_create(
    project: ProjectSpec,
    root: Path,
    state_root: Path,
    outputs: Mapping[str, tuple[Path, ...]],
    names: Sequence[str] = (),
) -> tuple[Path, ...]:
    """Create selected archives with stable ordering, metadata, and timestamps."""
    selected = [item for item in project.artifacts if not names or item.name in names]
    unknown = set(names) - {item.name for item in selected}
    if unknown:
        raise ExecutionError(f"Unknown artifacts: {', '.join(sorted(unknown))}")
    destination = state_root / "artifacts"
    destination.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for item in selected:
        files = sorted((_resolve_file(root, value, outputs) for value in item.files), key=lambda path: path.as_posix())
        for path in files:
            if not path.is_file():
                raise ExecutionError(f"Artifact input does not exist: {path}")
        suffix = ".zip" if item.format == "zip" else ".tar.gz"
        archive_path = destination / f"{item.name}{suffix}"
        if item.format == "zip":
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for path in files:
                    relative = f"{item.prefix}/{path.name}".lstrip("/")
                    zip_info = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
                    zip_info.external_attr = 0o100644 << 16
                    archive.writestr(zip_info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        else:
            with (
                archive_path.open("wb") as raw,
                gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
                tarfile.open(fileobj=compressed, mode="w") as archive,
            ):
                for path in files:
                    tar_info = archive.gettarinfo(str(path), f"{item.prefix}/{path.name}".lstrip("/"))
                    tar_info.mtime = 0
                    tar_info.uid = tar_info.gid = 0
                    tar_info.uname = tar_info.gname = ""
                    with path.open("rb") as source:
                        archive.addfile(tar_info, source)
        created.append(archive_path)
    return tuple(created)
