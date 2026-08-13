"""Self-contained Python zipapp distribution."""

from __future__ import annotations

import shutil
import tempfile
import zipapp
from pathlib import Path


def standalone_create(destination: Path) -> Path:
    """Create a standard-library-only Drift zipapp from this installation."""
    package_root = Path(__file__).resolve().parent
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="drift-standalone-") as temporary_text:
        root = Path(temporary_text)
        shutil.copytree(package_root, root / "driftbuild", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (root / "__main__.py").write_text(
            "from driftbuild.fast import main\nraise SystemExit(main())\n", encoding="ascii"
        )
        zipapp.create_archive(root, destination, interpreter="/usr/bin/env python3", compressed=True)
    return destination
