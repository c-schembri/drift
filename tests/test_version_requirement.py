from pathlib import Path

import pytest

from driftbuild.errors import ConfigurationError
from driftbuild.version_requirement import (
    project_requirement,
    project_requirement_validate,
    requirement_exact_version,
    requirement_satisfied,
)


def test_requirement_supports_exact_and_bounded_versions() -> None:
    assert requirement_satisfied(">=0.1,<0.2", "0.1.7")
    assert requirement_satisfied("==1.2.3", "1.2.3")
    assert not requirement_satisfied(">=2", "1.9.0")
    assert requirement_exact_version("==1.2.3") == "1.2.3"
    assert requirement_exact_version(">=1.2") is None


def test_project_requirement_is_read_and_enforced(tmp_path: Path) -> None:
    (tmp_path / "drift.toml").write_text(
        '[project]\napi-version = 1\nprovider = "build:project"\nrequires-drift = ">=99"\n', encoding="utf-8"
    )

    assert project_requirement(tmp_path) == ">=99"
    with pytest.raises(ConfigurationError, match="Project requires Drift"):
        project_requirement_validate(tmp_path)
