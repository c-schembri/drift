"""Install verified native Drift release archives without a system Python."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, cast

from driftbuild.errors import ExecutionError
from driftbuild.storage import drift_home


def _platform_asset() -> str:
    system = platform.system().casefold()
    architectures = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}
    machine_key = platform.machine().casefold()
    if machine_key not in architectures:
        raise ExecutionError(f"Native Drift releases do not support {platform.machine()}")
    machine = architectures[machine_key]
    names = {"windows": "windows", "darwin": "macos", "linux": "linux"}
    if system not in names:
        raise ExecutionError(f"Native Drift releases do not support {platform.system()}")
    name = names[system]
    return f"drift-{name}-{machine}.tar.gz"


def _download(url: str, destination: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except OSError as error:
        raise ExecutionError(f"Cannot download {url}: {error}") from error


def _release(repository: str, version: str | None) -> tuple[str, dict[str, str]]:
    endpoint = f"https://api.github.com/repos/{repository}/releases/{'tags/v' + version if version else 'latest'}"
    try:
        with urllib.request.urlopen(endpoint, timeout=30) as response:
            payload = json.load(response)
    except (OSError, json.JSONDecodeError) as error:
        raise ExecutionError(f"Cannot query Drift releases: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("tag_name"), str):
        raise ExecutionError("GitHub returned an invalid Drift release")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ExecutionError("Drift release has no assets")
    urls = {
        item["name"]: item["browser_download_url"]
        for value in assets
        if isinstance(value, dict)
        for item in (cast(dict[str, Any], value),)
        if isinstance(item.get("name"), str) and isinstance(item.get("browser_download_url"), str)
    }
    return payload["tag_name"].removeprefix("v"), urls


def _extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            if member.issym() or member.islnk() or member.isdev():
                raise ExecutionError(f"Unsafe release archive member: {member.name}")
            target = (destination / member.name).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as error:
                raise ExecutionError(f"Release archive path escapes destination: {member.name}") from error
        bundle.extractall(destination, filter="data")


def release_install(archive: Path, checksums: Path, version: str, home: Path | None = None) -> Path:
    """Install one verified native release and atomically update the user shim."""
    if re.fullmatch(r"[A-Za-z0-9._-]+", version) is None:
        raise ExecutionError(f"Invalid release version: {version!r}")
    expected: dict[str, str] = {}
    for line in checksums.read_text(encoding="ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if separator and len(digest) == 64:
            expected[name] = digest
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if expected.get(archive.name) != actual:
        raise ExecutionError(f"Release checksum mismatch for {archive.name}")
    root = (home or drift_home()).resolve()
    versions = root / "versions"
    destination = versions / f"{version}-{actual[:12]}"
    versions.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="drift-update-", dir=versions) as temporary_text:
        staged = Path(temporary_text) / "install"
        staged.mkdir()
        _extract(archive, staged)
        executable = staged / ("drift.exe" if os.name == "nt" else "drift")
        if not executable.is_file():
            raise ExecutionError(f"Release archive does not contain {executable.name}")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        if destination.exists():
            shutil.rmtree(staged)
        else:
            os.replace(staged, destination)
    bin_root = root / "bin"
    bin_root.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        shim = bin_root / "drift.cmd"
        temporary = shim.with_suffix(".tmp")
        temporary.write_text(f'@"{destination / "drift.exe"}" %*\r\n', encoding="ascii")
        os.replace(temporary, shim)
    else:
        shim = bin_root / "drift"
        temporary = shim.with_suffix(".tmp")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(destination / "drift")
        os.replace(temporary, shim)
    return shim


def self_update(
    repository: str = "c-schembri/drift",
    version: str | None = None,
    allowed_signers: Path | None = None,
    signer: str | None = None,
) -> tuple[str, Path]:
    """Download, verify, and install the requested native Drift release."""
    if (allowed_signers is None) != (signer is None):
        raise ExecutionError("Self-update signature verification requires both allowed signers and signer identity")
    selected_version, assets = _release(repository, version)
    asset = _platform_asset()
    if asset not in assets or "SHA256SUMS" not in assets:
        raise ExecutionError(f"Release v{selected_version} has no {asset} or SHA256SUMS asset")
    with tempfile.TemporaryDirectory(prefix="drift-download-") as temporary_text:
        root = Path(temporary_text)
        archive = root / asset
        checksums = root / "SHA256SUMS"
        _download(assets[asset], archive)
        _download(assets["SHA256SUMS"], checksums)
        if allowed_signers is not None and signer is not None:
            if "SHA256SUMS.sig" not in assets:
                raise ExecutionError(f"Release v{selected_version} has no SHA256SUMS.sig asset")
            _download(assets["SHA256SUMS.sig"], checksums.with_suffix(".sig"))
            from driftbuild.supply_chain import signature_verify

            signature_verify(checksums, allowed_signers, signer)
        shim = release_install(archive, checksums, selected_version)
    return selected_version, shim
