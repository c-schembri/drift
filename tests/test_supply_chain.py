from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from driftbuild.errors import ExecutionError
from driftbuild.model import ProjectSpec
from driftbuild.self_update import release_install
from driftbuild.supply_chain import audit_create, checksums_create


def test_audit_without_packages_is_deterministic(tmp_path: Path) -> None:
    first = audit_create(ProjectSpec("sample"), tmp_path, tmp_path / "first")
    second = audit_create(ProjectSpec("sample"), tmp_path, tmp_path / "second")

    assert first[0].read_bytes() == second[0].read_bytes()
    assert json.loads(first[0].read_text(encoding="utf-8"))["components"] == []
    assert first[1].read_text(encoding="utf-8") == ""


def test_checksums_are_sorted_and_release_install_is_verified(tmp_path: Path) -> None:
    executable_name = "drift.exe" if os.name == "nt" else "drift"
    archive = tmp_path / "drift-test.tar.gz"
    payload = b"native drift"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo(executable_name)
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    checksums = checksums_create((archive,), tmp_path / "SHA256SUMS")

    shim = release_install(archive, checksums, "1.2.3", tmp_path / "home")

    assert shim.exists()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    installed = tmp_path / "home" / "versions" / f"1.2.3-{digest[:12]}" / executable_name
    assert installed.read_bytes() == payload
    checksums.write_text(f"{'0' * 64}  {archive.name}\n", encoding="ascii")
    with pytest.raises(ExecutionError, match="checksum mismatch"):
        release_install(archive, checksums, "1.2.4", tmp_path / "home")


def test_checksums_use_file_names_and_sha256(tmp_path: Path) -> None:
    second = tmp_path / "b.bin"
    first = tmp_path / "a.bin"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    result = checksums_create((second, first), tmp_path / "SHA256SUMS")

    assert result.read_text(encoding="ascii").splitlines() == [
        f"{hashlib.sha256(b'a').hexdigest()}  a.bin",
        f"{hashlib.sha256(b'b').hexdigest()}  b.bin",
    ]


def test_release_install_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "drift-test.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("../escape")
        info.size = 1
        bundle.addfile(info, io.BytesIO(b"x"))
    checksums = checksums_create((archive,), tmp_path / "SHA256SUMS")

    with pytest.raises(ExecutionError, match="escapes destination"):
        release_install(archive, checksums, "1.2.3", tmp_path / "home")

    assert not (tmp_path / "escape").exists()
