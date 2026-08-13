"""Release validation and optional GitHub publication."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from driftbuild.errors import ExecutionError
from driftbuild.github import github_release_create
from driftbuild.model import ProjectSpec
from driftbuild.process import run


def release_publish(
    project: ProjectSpec,
    root: Path,
    artifact_paths: Sequence[Path],
    name: str,
    *,
    publish: bool = False,
    sign_key: Path | None = None,
) -> str:
    """Validate clean git state and publish a declared release when requested."""
    release = next((item for item in project.releases if item.name == name), None)
    if release is None:
        raise ExecutionError(f"Unknown release: {name}")
    status = run(["git", "status", "--porcelain"], cwd=root, capture=True)
    if status.stdout.strip():
        raise ExecutionError("Release requires a clean git worktree")
    tag = release.tag or f"v{release.version}"
    from driftbuild.supply_chain import checksums_create, signature_create

    checksum_path = checksums_create(artifact_paths, root / ".drift" / "artifacts" / "SHA256SUMS")
    publication_paths = [*artifact_paths, checksum_path]
    if sign_key is not None:
        publication_paths.append(signature_create(checksum_path, sign_key))
    if publish:
        if project.github is None:
            raise ExecutionError("Release publication requires GitHub configuration")
        github_release_create(project.github, tag, publication_paths, title=release.name, draft=release.draft)
    return tag
