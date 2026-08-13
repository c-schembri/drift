"""Named configuration matrix execution."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, cast

from driftbuild.build import build, build_timing_render
from driftbuild.errors import ExecutionError
from driftbuild.graph import project_validate
from driftbuild.model import BuildConfig, MatrixSpec
from driftbuild.packages import packages_compose
from driftbuild.project import project_load
from driftbuild.testing import tests_run


def configuration_apply(base: BuildConfig, values: dict[str, str]) -> BuildConfig:
    """Apply matrix-style values to one immutable build configuration."""
    definitions = dict(base.values)
    platform = base.platform
    architecture = base.architecture
    compiler = base.compiler
    build_type = base.build_type
    profile = base.profile
    for name, value in values.items():
        if name == "platform":
            platform = value
        elif name == "architecture":
            architecture = value
        elif name == "compiler":
            compiler = value
        elif name in ("build-type", "build_type"):
            build_type = value
        elif name == "profile":
            profile = cast(Any, value)
        else:
            definitions[name] = value
    return BuildConfig(
        platform,
        architecture,
        compiler,
        build_type,
        definitions,
        base.target,
        base.sysroot,
        base.toolchain_file,
        base.sanitizers,
        base.coverage,
        base.lto,
        base.warnings,
        base.unity_size,
        profile,
        base.hermetic,
    )


def matrix_run(
    matrix: MatrixSpec,
    root: Path,
    state_root: Path,
    base_config: BuildConfig,
    *,
    jobs: int | None = None,
    offline: bool = False,
) -> None:
    """Evaluate and execute every configuration in a declared matrix."""
    names = [name for name, _values in matrix.axes]
    combinations = itertools.product(*(values for _name, values in matrix.axes))
    for combination in combinations:
        selected = dict(zip(names, combination, strict=True))
        label = ", ".join(f"{name}={value}" for name, value in selected.items())
        print(f"[{matrix.name}] {label}", flush=True)
        config = configuration_apply(base_config, selected)
        project = packages_compose(project_load(root, config), root, config, offline=offline)
        project_validate(project)
        if matrix.operation == "build":
            build_result = build(project, root, state_root, config, matrix.targets, jobs=jobs)
            assert build_result.timing is not None
            print(build_timing_render(build_result.timing), flush=True)
        else:
            results = tests_run(project, root, state_root, config, matrix.targets, jobs=jobs)
            if not results:
                raise ExecutionError(f"Matrix {matrix.name} selected no tests")
            for test_result in results:
                print(f"PASS {test_result.name} ({test_result.duration_seconds:.3f}s)", flush=True)
