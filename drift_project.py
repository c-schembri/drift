"""Drift's own maintenance project declaration."""

from driftbuild.api import ArtifactSpec, GitHubSpec, ProjectApi, ReleaseSpec, TaskSpec, TestSpec


def project(api: ProjectApi):
    api.task(TaskSpec("lint", command=("uv", "run", "ruff", "check", ".")))
    api.task(TaskSpec("typecheck", command=("uv", "run", "mypy", "driftbuild")))
    api.task(TaskSpec("unit", command=("uv", "run", "pytest")))
    api.task(TaskSpec("check", dependencies=("lint", "typecheck", "unit")))
    api.test(TestSpec("unit", ("uv", "run", "pytest"), labels=("python",)))
    api.artifact(ArtifactSpec("source", api.files("pyproject.toml", "README.md", "LICENSE").files, "tar.gz", "drift"))
    api.release(ReleaseSpec("drift", "0.3.0", ("source",), tag="v0.3.0"))
    api.github(GitHubSpec("c-schembri/drift"))
    return api.project("drift")
