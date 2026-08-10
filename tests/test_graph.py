from pathlib import Path

import pytest

from driftbuild.errors import ConfigurationError
from driftbuild.graph import project_validate, transitive_targets
from driftbuild.model import ProjectSpec, TargetDependency, TargetRef, TargetSpec


def test_cycle_reports_the_complete_path() -> None:
    project = ProjectSpec(
        "cycle",
        targets=(
            TargetSpec("a", "static_library", dependencies=(TargetDependency(TargetRef("b"), "private"),)),
            TargetSpec("b", "static_library", dependencies=(TargetDependency(TargetRef("a"), "private"),)),
        ),
    )

    with pytest.raises(ConfigurationError, match="a -> b -> a"):
        project_validate(project)


def test_transitive_targets_include_artifact_producers() -> None:
    targets = {
        "generated": TargetSpec("generated", "custom", outputs=(Path("generated.c"),)),
        "app": TargetSpec("app", "executable", dependencies=(TargetDependency(TargetRef("generated"), "private"),)),
    }

    assert transitive_targets(targets, ("app",)) == {"app", "generated"}
