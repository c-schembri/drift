"""Pinned Ninja bootstrap with checksum verification and atomic installation."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from driftbuild.errors import ConfigurationError

NINJA_VERSION = "1.13.1"
_ARCHIVES = {
    "nt": (
        "ninja-win.zip",
        "26a40fa8595694dec2fad4911e62d29e10525d2133c9a4230b66397774ae25bf",
        "ninja.exe",
        "2df494116decdae84d74c02373119176bbfc4797a6f49a6c709d85d961d6a0d8",
    ),
    "posix": (
        "ninja-linux.zip",
        "0830252db77884957a1a4b87b05a1e2d9b5f658b8367f82999a941884cbe0238",
        "ninja",
        "c3957e61c0da673122c8dd84e2418923aebee905f5629e01decb1ef298b9ac49",
    ),
}


def ninja_resolve(state_root: Path, override: str | None = None) -> Path:
    """Return a verified pinned Ninja, downloading it into project state when absent."""
    selected = override or os.environ.get("DRIFT_NINJA")
    if selected:
        executable = Path(selected).expanduser().resolve()
        if not executable.is_file():
            raise ConfigurationError(f"DRIFT_NINJA does not name a file: {executable}")
        return executable

    platform_key = "nt" if os.name == "nt" else "posix"
    archive_name, checksum, executable_name, executable_checksum = _ARCHIVES[platform_key]
    install = state_root / "tools" / "ninja" / NINJA_VERSION
    executable = install / executable_name
    if executable.is_file():
        actual = hashlib.sha256(executable.read_bytes()).hexdigest()
        if actual == executable_checksum:
            return executable
        raise ConfigurationError(f"Cached Ninja checksum mismatch at {executable}; remove the cached tool and retry")

    install.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/ninja-build/ninja/releases/download/v{NINJA_VERSION}/{archive_name}"
    with tempfile.TemporaryDirectory(prefix="drift-ninja-", dir=install.parent) as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / archive_name
        try:
            with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        except OSError as error:
            raise ConfigurationError(f"Cannot download pinned Ninja {NINJA_VERSION}: {error}") from error
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != checksum:
            raise ConfigurationError(f"Ninja archive checksum mismatch: expected {checksum}, got {actual}")
        with zipfile.ZipFile(archive) as bundle:
            member = bundle.getinfo(executable_name)
            bundle.extract(member, temporary_path / "unpacked")
        staged = temporary_path / "unpacked" / executable_name
        staged.chmod(staged.stat().st_mode | stat.S_IXUSR)
        try:
            install.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            pass
        if not executable.exists():
            os.replace(staged, executable)
    return executable
