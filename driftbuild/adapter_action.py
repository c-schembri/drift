"""Run an imported build-system action and refresh its completion marker."""

from __future__ import annotations

import argparse
from pathlib import Path

from driftbuild.locking import cache_lock
from driftbuild.process import run


def main() -> int:
    """Run the command after ``--`` and touch the declared stamp."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--stamp", type=Path, required=True)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    def execute() -> None:
        run(command)
        arguments.stamp.parent.mkdir(parents=True, exist_ok=True)
        arguments.stamp.touch()

    if arguments.lock is None:
        execute()
    else:
        with cache_lock(arguments.lock):
            execute()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
