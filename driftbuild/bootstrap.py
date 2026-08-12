"""Pinned third-party build-tool bootstrap and verification."""

from __future__ import annotations

import hashlib
import importlib
import os
import platform
import shlex
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import venv
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath

from driftbuild.errors import ConfigurationError
from driftbuild.storage import tool_store_root

NINJA_VERSION = "1.13.1"
CMAKE_VERSION = "3.31.6"
MESON_VERSION = "1.12.0"
CONAN_VERSION = "2.31.2"

_MESON_WHEEL = (
    "https://files.pythonhosted.org/packages/07/68/"
    "b0117422eb0a46d9d8d9e328f0c5b5c835179bfc058688bca35c90c89eba/meson-1.12.0-py3-none-any.whl"
)
_MESON_WHEEL_SHA256 = "71f133147fa0fcfe8f4df49fa1045771064947834538409e5d97b3613aac8b4e"
_MESON_ENTRY_SHA256 = "a4c127505025b493916c4c8b8dee6a12c0fae5380f6eb8a7ccf0e39ca6f4750e"
_MESON_LAUNCHER = "import sys\nfrom mesonbuild.mesonmain import main\nsys.exit(main())\n"

_CONAN_PACKAGES = (
    f"conan=={CONAN_VERSION}",
    "requests==2.34.2",
    "urllib3==2.7.0",
    "colorama==0.4.6",
    "PyYAML==6.0.3",
    "patch-ng==1.19.1",
    "fasteners==0.20",
    "Jinja2==3.1.6",
    "python-dateutil==2.9.0.post0",
    "distro==1.9.0",
    "certifi==2026.7.22",
    "charset-normalizer==3.4.9",
    "idna==3.18",
    "MarkupSafe==3.0.3",
    "six==1.17.0",
)
_CONAN_INSTALL_MARKER = f"{CONAN_VERSION}:1"


def _meson_wrapper_write(install: Path) -> Path:
    launcher = install / "drift-meson.py"
    wrapper = install / ("meson.cmd" if os.name == "nt" else "meson")
    if os.name == "nt":
        content = f'@"{Path(sys.executable).resolve()}" "{launcher.resolve()}" %*\r\n'
    else:
        content = (
            f"#!/bin/sh\nexec {shlex.quote(str(Path(sys.executable).resolve()))} "
            f'{shlex.quote(str(launcher.resolve()))} "$@"\n'
        )
    encoded = content.encode("ascii")
    if not wrapper.is_file() or wrapper.read_bytes() != encoded:
        wrapper.write_bytes(encoded)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return wrapper


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


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            unlock = partial(msvcrt.locking, stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl = importlib.import_module("fcntl")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            unlock = partial(fcntl.flock, stream.fileno(), fcntl.LOCK_UN)
        try:
            yield
        finally:
            stream.seek(0)
            unlock()


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
    _state_root: Path,
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
    install = tool_store_root() / tool / version
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


def meson_command(_state_root: Path, override: str | None = None) -> tuple[str, ...]:
    """Return a command for the verified, pinned Meson Python wheel."""
    selected = override or os.environ.get("DRIFT_MESON")
    if selected:
        executable = Path(selected).expanduser().resolve()
        if not executable.is_file():
            raise ConfigurationError(f"DRIFT_MESON does not name a file: {executable}")
        return (str(executable),)

    install = tool_store_root() / "meson" / MESON_VERSION
    entry = install / "mesonbuild" / "mesonmain.py"
    launcher = install / "drift-meson.py"
    wrapper = install / ("meson.cmd" if os.name == "nt" else "meson")
    if entry.is_file() and launcher.is_file():
        actual = hashlib.sha256(entry.read_bytes()).hexdigest()
        if actual != _MESON_ENTRY_SHA256:
            raise ConfigurationError(f"Cached Meson checksum mismatch at {entry}; remove it and retry")
        if not wrapper.is_file():
            _meson_wrapper_write(install)
        return (os.fspath(Path(sys.executable).resolve()), os.fspath(launcher))

    install.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="drift-meson-", dir=install.parent) as temporary:
        temporary_path = Path(temporary)
        wheel = temporary_path / f"meson-{MESON_VERSION}.whl"
        try:
            with urllib.request.urlopen(_MESON_WHEEL, timeout=120) as response:
                with wheel.open("wb") as output:
                    shutil.copyfileobj(response, output)
        except OSError as error:
            raise ConfigurationError(f"Cannot download pinned Meson {MESON_VERSION}: {error}") from error
        actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if actual != _MESON_WHEEL_SHA256:
            raise ConfigurationError(
                f"Meson wheel checksum mismatch: expected {_MESON_WHEEL_SHA256}, got {actual}"
            )
        unpacked = temporary_path / "unpacked"
        _archive_extract(wheel, unpacked)
        (unpacked / "drift-meson.py").write_text(_MESON_LAUNCHER, encoding="ascii")
        try:
            os.replace(unpacked, install)
        except FileExistsError:
            pass
    _meson_wrapper_write(install)
    return (os.fspath(Path(sys.executable).resolve()), os.fspath(launcher))


def meson_resolve(state_root: Path, override: str | None = None) -> Path:
    """Return an executable wrapper for the managed Meson wheel."""
    command = meson_command(state_root, override)
    if len(command) == 1:
        return Path(command[0])
    return tool_store_root() / "meson" / MESON_VERSION / ("meson.cmd" if os.name == "nt" else "meson")


def conan_resolve(_state_root: Path, override: str | None = None) -> Path:
    """Return the pinned Conan CLI from an isolated managed environment."""
    selected = override or os.environ.get("DRIFT_CONAN")
    if selected:
        executable = Path(selected).expanduser().resolve()
        if not executable.is_file():
            raise ConfigurationError(f"DRIFT_CONAN does not name a file: {executable}")
        return executable

    install = tool_store_root() / "conan" / CONAN_VERSION
    executable = install / ("Scripts/conan.exe" if os.name == "nt" else "bin/conan")
    marker = install / ".drift-version"
    if executable.is_file() and marker.is_file() and marker.read_text(encoding="ascii").strip() == _CONAN_INSTALL_MARKER:
        return executable

    install.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(install.parent / f".{CONAN_VERSION}.lock"):
        if (
            executable.is_file()
            and marker.is_file()
            and marker.read_text(encoding="ascii").strip() == _CONAN_INSTALL_MARKER
        ):
            return executable
        try:
            install.resolve().relative_to((tool_store_root() / "conan").resolve())
        except ValueError as error:
            raise ConfigurationError(f"Unsafe managed Conan installation path: {install}") from error
        if install.exists():
            shutil.rmtree(install)
        try:
            venv.EnvBuilder(with_pip=True, clear=True).create(install)
        except OSError as error:
            raise ConfigurationError(f"Cannot create the managed Conan environment: {error}") from error
        python = install / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        from driftbuild.process import run

        run(
            (
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--only-binary=:all:",
                *_CONAN_PACKAGES,
            ),
            capture=True,
            timeout_seconds=300,
        )
        marker.write_text(_CONAN_INSTALL_MARKER + "\n", encoding="ascii")
    if not executable.is_file():
        raise ConfigurationError(f"Pinned Conan installation did not produce {executable}")
    return executable
