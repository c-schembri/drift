from pathlib import Path

import pytest

from driftbuild.errors import ConfigurationError
from driftbuild.graph import project_validate, transitive_targets
from driftbuild.model import (
    CommandSpec,
    Deployment,
    MatrixSpec,
    ProjectSpec,
    SuiteSpec,
    TargetDependency,
    TargetRef,
    TargetSpec,
    TaskSpec,
)


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


def test_graph_rejects_unsafe_runtime_destinations() -> None:
    project = ProjectSpec(
        "unsafe",
        targets=(
            TargetSpec(
                "app",
                "executable",
                runtime_files=(Deployment(Path("runtime.dll"), Path("../runtime.dll")),),
            ),
        ),
    )

    with pytest.raises(ConfigurationError, match="relative file path"):
        project_validate(project)


def test_matrix_targets_are_validated_for_the_selected_operation() -> None:
    project = ProjectSpec(
        "matrix",
        matrices=(MatrixSpec("client", (("build-type", ("debug",)),), targets=("missing",)),),
    )

    with pytest.raises(ConfigurationError, match="unknown build targets"):
        project_validate(project)


def test_suite_tasks_cannot_recursively_reference_suites() -> None:
    project = ProjectSpec(
        "recursive",
        suites=(SuiteSpec("full", (TaskSpec("again", test="full"),)),),
    )

    with pytest.raises(ConfigurationError, match="references unknown test: full"):
        project_validate(project)


def test_run_command_can_declaratively_launch_one_target() -> None:
    project = ProjectSpec(
        "sample",
        targets=(TargetSpec("app", "executable"),),
        commands=(CommandSpec(("run", "client"), "Run the client", run_target=TargetRef("app")),),
    )

    assert project_validate(project)["app"].kind == "executable"


def test_run_target_rejects_non_run_commands() -> None:
    project = ProjectSpec(
        "sample",
        targets=(TargetSpec("app", "executable"),),
        commands=(CommandSpec(("deploy",), "Deploy", run_target=TargetRef("app")),),
    )

    with pytest.raises(ConfigurationError, match="run_target requires a run command"):
        project_validate(project)
