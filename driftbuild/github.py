"""GitHub automation through the authenticated gh CLI."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from driftbuild.model import GitHubSpec
from driftbuild.process import ProcessResult, run


def github_release_create(
    spec: GitHubSpec,
    tag: str,
    artifacts: Sequence[Path],
    *,
    title: str | None = None,
    notes: str | None = None,
    draft: bool = True,
) -> ProcessResult:
    """Create a GitHub release and upload explicit artifacts."""
    arguments = ["gh", "release", "create", tag, "--repo", spec.repository]
    if title:
        arguments.extend(("--title", title))
    if notes:
        arguments.extend(("--notes", notes))
    else:
        arguments.append("--generate-notes")
    if draft:
        arguments.append("--draft")
    arguments.extend(str(path) for path in artifacts)
    return run(arguments, capture=True)
