"""Internal runtime bundle assembler used by generated Ninja files."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


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
    for entry in entries:
        source = Path(entry["source"])
        target = destination / entry["destination"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
    stamp = Path(payload["stamp"])
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
