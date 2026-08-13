"""Build the native Drift application directory used by releases."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.__main__ import run

arguments = [
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name",
    "drift",
    "--collect-submodules",
    "driftbuild",
    "--collect-submodules",
    "mesonbuild",
    "--collect-submodules",
    "pip",
    "--collect-submodules",
    "unittest",
]
if os.name == "nt":
    stable_abi = Path(sys.base_prefix) / "python3.dll"
    if not stable_abi.is_file():
        raise RuntimeError(f"Python stable ABI library is missing: {stable_abi}")
    arguments.extend(("--add-binary", f"{stable_abi}{os.pathsep}."))
arguments.append("drift.py")
run(arguments)
