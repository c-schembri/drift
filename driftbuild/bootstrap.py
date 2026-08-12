"""Pinned third-party build-tool bootstrap and verification."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from driftbuild.errors import ConfigurationError

NINJA_VERSION = "1.13.1"
CMAKE_VERSION = "3.31.6"


@dataclass(frozen=True)
class _Archive:
    name: str
    sha256: str
    executable: Path
    executable_sha256: str
    root: Path | None = None


_NINJA_ARCHIVES = {
    ("win32", "x86_64"): _Archive(
        "ninja-win.zip",
        "26a40fa8595694dec2fad4911e62d29e10525d2133c9a4230b66397774ae25bf",
        Path("ninja.exe"),
        "2df494116decdae84d74c02373119176bbfc4797a6f49a6c709d85d961d6a0d8",
    ),
    ("win32", "arm64"): _Archive(
        "ninja-winarm64.zip",
        "fb959b674970e36a7c9a23191524b80fb5298fc71fc98bfa42456bcc0a8dfb2f",
        Path("ninja.exe"),
        "05e5471dcbae8d6a2d8fa9047855cec6010915ba6f45aa45c8b3ebfc596009bb",
    ),
    ("linux", "x86_64"): _Archive(
        "ninja-linux.zip",
        "0830252db77884957a1a4b87b05a1e2d9b5f658b8367f82999a941884cbe0238",
        Path("ninja"),
        "c3957e61c0da673122c8dd84e2418923aebee905f5629e01decb1ef298b9ac49",
    ),
    ("linux", "arm64"): _Archive(
        "ninja-linux-aarch64.zip",
        "740f1b9f9d8ae68438240a6a2f3f7a27fc8b1946d2024a6a6b25857ee877987b",
        Path("ninja"),
        "8a7d51e3484a3c4db6544169c31f1f958d4134a6faaa1ed3673957f7d44803dc",
    ),
    ("darwin", "x86_64"): _Archive(
        "ninja-mac.zip",
        "da7797794153629aca5570ef7c813342d0be214ba84632af886856e8f0063dd9",
        Path("ninja"),
        "d05246cceee4c3f19f4e9363ce0bdaf766e9e1f6bda139e30e04b234c667c059",
    ),
    ("darwin", "arm64"): _Archive(
        "ninja-mac.zip",
        "da7797794153629aca5570ef7c813342d0be214ba84632af886856e8f0063dd9",
        Path("ninja"),
        "d05246cceee4c3f19f4e9363ce0bdaf766e9e1f6bda139e30e04b234c667c059",
    ),
}

_CMAKE_ARCHIVES = {
    ("win32", "x86_64"): _Archive(
        "cmake-3.31.6-windows-x86_64.zip",
        "d163cd3ab4959b0a53fa8988f2ddbd2e6c501658201e6a154386bad9dbe4f836",
        Path("bin/cmake.exe"),
        "3fe22eb02e1c6184ec207366ed21a6f2f9c3828c1e9f3324546befadfb362be3",
        Path("cmake-3.31.6-windows-x86_64"),
    ),
    ("win32", "arm64"): _Archive(
        "cmake-3.31.6-windows-arm64.zip",
        "fa648fd417f44e6cb08928964a480ade0d18df421f9b623639dba22f9b301e4e",
        Path("bin/cmake.exe"),
        "e969e5b5ba1c15bfae179f67f2349d60db7c7beaeb558dc0368cfe83c80576fc",
        Path("cmake-3.31.6-windows-arm64"),
    ),
    ("linux", "x86_64"): _Archive(
        "cmake-3.31.6-linux-x86_64.tar.gz",
        "5a1133ff103c71eb5120e2cc3de922733e7d8a26a98ae716397e8676adb367bf",
        Path("bin/cmake"),
        "c4b3b237dc7a013590db9e90f70fca2dfdedde09521e920d3339569ea364230a",
        Path("cmake-3.31.6-linux-x86_64"),
    ),
    ("linux", "arm64"): _Archive(
        "cmake-3.31.6-linux-aarch64.tar.gz",
        "b4cc788d63112b2749b40627e719eb5d3b8ed8f00c36d77189f4019cfe64bc9e",
        Path("bin/cmake"),
        "52b0d0363cfbe58f39285503658d0f7f2796b3149d8982315d2e57c293838837",
        Path("cmake-3.31.6-linux-aarch64"),
    ),
    ("darwin", "x86_64"): _Archive(
        "cmake-3.31.6-macos-universal.tar.gz",
        "330b9514f5112e5ed4fb08b8b05803b776fd9b539a6ae12927d14dcc0ee2ba8d",
        Path("CMake.app/Contents/bin/cmake"),
        "197c200f4356c5e5327836708751ec37f441db469ecba1fcc7075e786f48cb1a",
        Path("cmake-3.31.6-macos-universal"),
    ),
    ("darwin", "arm64"): _Archive(
        "cmake-3.31.6-macos-universal.tar.gz",
        "330b9514f5112e5ed4fb08b8b05803b776fd9b539a6ae12927d14dcc0ee2ba8d",
        Path("CMake.app/Contents/bin/cmake"),
        "197c200f4356c5e5327836708751ec37f441db469ecba1fcc7075e786f48cb1a",
        Path("cmake-3.31.6-macos-universal"),
    ),
}


def _host_key() -> tuple[str, str]:
    system = platform.system().casefold()
    operating_system = "win32" if system == "windows" else "darwin" if system == "darwin" else "linux"
    machine = platform.machine().casefold()
    architecture = "arm64" if machine in ("arm64", "aarch64") else "x86_64" if machine in ("amd64", "x86_64") else machine
    return operating_system, architecture


def _archive_extract(archive: Path, destination: Path) -> None:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path = PurePosixPath(member.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts:
                    raise ConfigurationError(f"Tool archive member escapes its root: {member.filename}")
            bundle.extractall(destination)
        return
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(destination, filter="data")
    except (OSError, tarfile.TarError) as error:
        raise ConfigurationError(f"Cannot extract tool archive {archive.name}: {error}") from error


def _tool_resolve(
    state_root: Path,
    tool: str,
    version: str,
    release_url: str,
    archives: dict[tuple[str, str], _Archive],
    override: str | None,
) -> Path:
    selected = override or os.environ.get(f"DRIFT_{tool.upper()}")
    if selected:
        executable = Path(selected).expanduser().resolve()
        if not executable.is_file():
            raise ConfigurationError(f"DRIFT_{tool.upper()} does not name a file: {executable}")
        return executable

    host = _host_key()
    description = archives.get(host)
    if description is None:
        raise ConfigurationError(f"Pinned {tool} {version} is unavailable for {host[0]} {host[1]}")
    install = state_root / "tools" / tool / version
    executable = install / description.executable
    if executable.is_file():
        actual = hashlib.sha256(executable.read_bytes()).hexdigest()
        if actual == description.executable_sha256:
            return executable
        raise ConfigurationError(f"Cached {tool} checksum mismatch at {executable}; remove it and retry")

    install.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"drift-{tool}-", dir=install.parent) as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / description.name
        try:
            with urllib.request.urlopen(f"{release_url}/{description.name}", timeout=120) as response:
                with archive.open("wb") as output:
                    shutil.copyfileobj(response, output)
        except OSError as error:
            raise ConfigurationError(f"Cannot download pinned {tool} {version}: {error}") from error
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != description.sha256:
            raise ConfigurationError(f"{tool} archive checksum mismatch: expected {description.sha256}, got {actual}")
        unpacked = temporary_path / "unpacked"
        _archive_extract(archive, unpacked)
        source = unpacked / description.root if description.root is not None else unpacked
        staged_executable = source / description.executable
        staged_executable.chmod(staged_executable.stat().st_mode | stat.S_IXUSR)
        actual = hashlib.sha256(staged_executable.read_bytes()).hexdigest()
        if actual != description.executable_sha256:
            raise ConfigurationError(
                f"{tool} executable checksum mismatch: expected {description.executable_sha256}, got {actual}"
            )
        try:
            os.replace(source, install)
        except FileExistsError:
            pass
    if not executable.is_file():
        raise ConfigurationError(f"Pinned {tool} installation did not produce {executable}")
    return executable


def ninja_resolve(state_root: Path, override: str | None = None) -> Path:
    """Return a verified pinned Ninja for the current host."""
    return _tool_resolve(
        state_root,
        "ninja",
        NINJA_VERSION,
        f"https://github.com/ninja-build/ninja/releases/download/v{NINJA_VERSION}",
        _NINJA_ARCHIVES,
        override,
    )


def cmake_resolve(state_root: Path, override: str | None = None) -> Path:
    """Return a verified pinned CMake distribution for the current host."""
    return _tool_resolve(
        state_root,
        "cmake",
        CMAKE_VERSION,
        f"https://github.com/Kitware/CMake/releases/download/v{CMAKE_VERSION}",
        _CMAKE_ARCHIVES,
        override,
    )
