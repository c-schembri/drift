"""Internal runtime bundle assembler used by generated Ninja files."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def _relative_destination(value: object) -> Path:
    if not isinstance(value, str):
        raise ValueError("Runtime bundle destinations must be strings")
    destination = Path(value)
    if destination.is_absolute() or destination in (Path(""), Path(".")) or ".." in destination.parts:
        raise ValueError(f"Invalid runtime bundle destination: {value}")
    return destination


def _owned_parents(destination: Path, value: str) -> tuple[Path, ...]:
    parents = []
    for parent in (destination / value).parents:
        if parent == destination:
            break
        parents.append(parent)
    return tuple(parents)


def main() -> int:
    """Copy explicitly declared runtime files and update the target stamp."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    arguments = parser.parse_args()
    payload: dict[str, Any] = json.loads(arguments.spec.read_text(encoding="utf-8"))
    destination = Path(payload["destination"])
    destination.mkdir(parents=True, exist_ok=True)
    entries = payload.get("entries")
    if entries is None:
        entries = ({"source": source, "destination": Path(source).name} for source in payload["files"])
    entries = tuple(entries)
    manifest = Path(payload.get("manifest", str(payload["stamp"]) + ".json"))
    desired = {_relative_destination(entry["destination"]).as_posix() for entry in entries}
    if payload.get("clean", False) and manifest.is_file():
        previous: object = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(previous, list) or not all(isinstance(value, str) for value in previous):
            raise ValueError(f"Invalid runtime bundle manifest: {manifest}")
        for value in previous:
            _relative_destination(value)
            if value in desired:
                continue
            stale = destination / value
            if stale.is_file() or stale.is_symlink():
                stale.unlink()
        directories = sorted(
            {parent for value in previous for parent in _owned_parents(destination, value)},
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    for entry in entries:
        source = Path(entry["source"])
        target = destination / _relative_destination(entry["destination"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(sorted(desired), indent=2) + "\n", encoding="utf-8")
    stamp = Path(payload["stamp"])
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
