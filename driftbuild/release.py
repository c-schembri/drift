"""Release validation and optional GitHub publication."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from driftbuild.errors import ExecutionError
from driftbuild.github import github_release_create
from driftbuild.model import ProjectSpec
from driftbuild.process import run


def release_publish(
    project: ProjectSpec, root: Path, artifact_paths: Sequence[Path], name: str, *, publish: bool = False
) -> str:
    """Validate clean git state and publish a declared release when requested."""
    release = next((item for item in project.releases if item.name == name), None)
    if release is None:
        raise ExecutionError(f"Unknown release: {name}")
    status = run(["git", "status", "--porcelain"], cwd=root, capture=True)
    if status.stdout.strip():
        raise ExecutionError("Release requires a clean git worktree")
    tag = release.tag or f"v{release.version}"
    if publish:
        if project.github is None:
            raise ExecutionError("Release publication requires GitHub configuration")
        github_release_create(project.github, tag, artifact_paths, title=release.name, draft=release.draft)
    return tag
