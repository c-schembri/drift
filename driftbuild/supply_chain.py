"""Deterministic SBOM, license evidence, checksums, and SSH signatures."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from driftbuild.errors import ExecutionError
from driftbuild.model import GitSource, ProjectSpec
from driftbuild.packages import package_lock_read, package_store_root

_LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "COPYRIGHT")


def _license_files(root: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix())
        if path.is_file()
        and len(path.relative_to(root).parts) <= 3
        and any(path.name.upper().startswith(name) for name in _LICENSE_NAMES)
    )


def audit_create(project: ProjectSpec, root: Path, output: Path) -> tuple[Path, Path]:
    """Create a CycloneDX SBOM and a bundled third-party license report."""
    lock = package_lock_read(root) if (root / "drift.lock").is_file() else None
    components: list[dict[str, Any]] = []
    license_sections: list[str] = []
    for package in lock.packages if lock is not None else ():
        source_root = package_store_root() / "sources" / package.content_sha256
        if not source_root.is_dir():
            raise ExecutionError(f"Package source is not cached; run 'drift fetch': {package.name}")
        evidence = _license_files(source_root)
        reference = "/".join(part for part in (package.scope, package.name) if part)
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": f"{reference}@{package.content_sha256}",
            "name": package.name,
            "version": package.source.revision if isinstance(package.source, GitSource) else package.content_sha256[:12],
            "hashes": [{"alg": "SHA-256", "content": package.content_sha256}],
            "properties": [
                {"name": "drift:scope", "value": package.scope},
                {"name": "drift:request-sha256", "value": package.request_sha256},
            ],
        }
        source_url = getattr(package.source, "url", None) or getattr(package.source, "registry", None)
        if isinstance(source_url, str):
            component["externalReferences"] = [{"type": "vcs", "url": source_url}]
        if evidence:
            component["evidence"] = {
                "licenses": [
                    {
                        "license": {"name": path.name},
                        "properties": [
                            {
                                "name": "drift:file-sha256",
                                "value": hashlib.sha256(path.read_bytes()).hexdigest(),
                            }
                        ],
                    }
                    for path in evidence
                ]
            }
        components.append(component)
        for path in evidence:
            relative = path.relative_to(source_root).as_posix()
            content = path.read_text(encoding="utf-8", errors="replace")
            license_sections.append(f"===== {reference}: {relative} =====\n\n{content.rstrip()}\n")
    identity = hashlib.sha256(
        json.dumps(components, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'drift:' + identity)}",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": project.name}},
        "components": components,
    }
    output.mkdir(parents=True, exist_ok=True)
    sbom_path = output / "sbom.cdx.json"
    licenses_path = output / "THIRD_PARTY_LICENSES.txt"
    sbom_path.write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    licenses_path.write_text("\n".join(license_sections), encoding="utf-8")
    return sbom_path, licenses_path


def checksums_create(files: Sequence[Path], output: Path) -> Path:
    """Write sorted SHA-256 checksums for explicit files."""
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in sorted(files)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="ascii")
    return output


def signature_create(path: Path, key: Path) -> Path:
    """Create a detached SSH signature using the system ssh-keygen."""
    executable = shutil.which("ssh-keygen")
    if executable is None:
        raise ExecutionError("Signing requires ssh-keygen")
    signature = path.with_suffix(path.suffix + ".sig")
    if signature.is_file():
        signature.unlink()
    completed = subprocess.run(
        (executable, "-Y", "sign", "-f", str(key), "-n", "drift", str(path)),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ExecutionError(f"Cannot sign {path}: {completed.stderr.strip()}")
    return signature


def signature_verify(path: Path, allowed_signers: Path, identity: str) -> None:
    """Verify a detached SSH signature against an allowed-signers file."""
    executable = shutil.which("ssh-keygen")
    signature = path.with_suffix(path.suffix + ".sig")
    if executable is None:
        raise ExecutionError("Signature verification requires ssh-keygen")
    if not signature.is_file():
        raise ExecutionError(f"Signature does not exist: {signature}")
    completed = subprocess.run(
        (
            executable,
            "-Y",
            "verify",
            "-f",
            str(allowed_signers),
            "-I",
            identity,
            "-n",
            "drift",
            "-s",
            str(signature),
        ),
        input=path.read_bytes(),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ExecutionError(f"Invalid signature for {path}: {detail}")
