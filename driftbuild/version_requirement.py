"""Project-level Drift version requirements."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from driftbuild import __version__
from driftbuild.errors import ConfigurationError

_CLAUSE = re.compile(r"(==|>=|<=|>|<)\s*(\d+(?:\.\d+){0,2})")


def _version(value: str) -> tuple[int, int, int]:
    core = value.split("+", 1)[0].split("-", 1)[0]
    if re.fullmatch(r"\d+(?:\.\d+){0,2}", core) is None:
        raise ConfigurationError(f"Unsupported Drift version: {value!r}")
    parts = [int(part) for part in core.split(".")]
    return tuple((*parts, 0, 0)[:3])  # type: ignore[return-value]


def requirement_satisfied(requirement: str, version: str = __version__) -> bool:
    """Return whether one Drift version satisfies a comma-separated constraint."""
    selected = _version(version)
    clauses = [part.strip() for part in requirement.split(",") if part.strip()]
    if not clauses:
        raise ConfigurationError("project.requires-drift cannot be empty")
    for clause in clauses:
        match = _CLAUSE.fullmatch(clause)
        if match is None:
            raise ConfigurationError(f"Unsupported project.requires-drift clause: {clause!r}")
        operator, required_text = match.groups()
        required = _version(required_text)
        accepted = {
            "==": selected == required,
            ">=": selected >= required,
            "<=": selected <= required,
            ">": selected > required,
            "<": selected < required,
        }[operator]
        if not accepted:
            return False
    return True


def project_requirement(root: Path) -> str | None:
    """Read the optional Drift requirement without evaluating the provider."""
    path = root / "drift.toml"
    try:
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"Cannot read {path}: {error}") from error
    project = manifest.get("project")
    if not isinstance(project, dict):
        raise ConfigurationError("drift.toml requires a [project] table")
    requirement = project.get("requires-drift")
    if requirement is not None and not isinstance(requirement, str):
        raise ConfigurationError("project.requires-drift must be a version constraint string")
    return requirement


def project_requirement_validate(root: Path) -> None:
    """Reject a project whose declared Drift constraint excludes this runtime."""
    requirement = project_requirement(root)
    if requirement is not None and not requirement_satisfied(requirement):
        raise ConfigurationError(
            f"Project requires Drift {requirement}, but this executable is {__version__}; run drift bootstrap --install"
        )


def requirement_exact_version(requirement: str) -> str | None:
    """Return an exact installable version when the requirement is one equality clause."""
    match = _CLAUSE.fullmatch(requirement.strip())
    return match.group(2) if match is not None and match.group(1) == "==" else None
