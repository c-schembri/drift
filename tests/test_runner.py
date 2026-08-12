from pathlib import Path

import pytest

from driftbuild.errors import ExecutionError
from driftbuild.model import ProjectSpec, TargetRef, TargetSpec
from driftbuild.runner import executable_select


def test_selects_executable_reachable_from_defaults() -> None:
    project = ProjectSpec(
        "sample",
        targets=(
            TargetSpec("tool", "executable"),
            TargetSpec("app", "executable"),
            TargetSpec("all", "alias", objects=(TargetRef("app"),)),
        ),
        defaults=(TargetRef("all"),),
    )

    assert executable_select(project).name == "app"


def test_requires_target_when_multiple_executables_are_equally_selectable() -> None:
    project = ProjectSpec(
        "sample",
        targets=(TargetSpec("one", "executable"), TargetSpec("two", "executable")),
    )

    with pytest.raises(ExecutionError, match="multiple executable targets; specify one: one, two"):
        executable_select(project)


def test_explicit_target_must_be_executable() -> None:
    project = ProjectSpec("sample", targets=(TargetSpec("library", "static_library", outputs=(Path("x"),)),))

    with pytest.raises(ExecutionError, match="not executable"):
        executable_select(project, "library")
