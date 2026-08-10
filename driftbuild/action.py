"""Internal custom-action process wrapper used by generated Ninja files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from driftbuild.process import run


def _expand(value: str, payload: dict[str, Any]) -> str:
    if value == "{root}":
        return str(payload["root"])
    if value == "{build}":
        return str(payload["build_root"])
    if value == "{out}":
        return str(payload["outputs"][0])
    if value.startswith("{out:") and value.endswith("}"):
        return str(payload["outputs"][int(value[5:-1])])
    if value.startswith("{in:") and value.endswith("}"):
        return str(payload["inputs"][int(value[4:-1])])
    return value


def main() -> int:
    """Execute one generated action specification."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    arguments = parser.parse_args()
    payload: dict[str, Any] = json.loads(arguments.spec.read_text(encoding="utf-8"))
    environment = dict(os.environ)
    environment.update(payload["environment"])
    for output in payload["outputs"]:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
    command = [_expand(value, payload) for value in payload["command"]]
    result = run(
        command,
        cwd=Path(payload["root"]),
        environment=environment,
        timeout_seconds=payload["timeout_seconds"],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
