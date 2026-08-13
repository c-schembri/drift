import importlib
import os
import sys
from pathlib import Path

import pytest

from driftbuild.errors import ExecutionError
from driftbuild.model import ProjectSpec
from driftbuild.python_environment import _identity, python_environment_activate


def test_cached_project_python_environment_is_added_to_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIFT_HOME", str(tmp_path / "home"))
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("sample==1.0\n", encoding="utf-8")
    project = ProjectSpec("sample", python_requirements=(requirements,))
    destination = tmp_path / "home/python" / _identity(project.python_requirements)
    destination.mkdir(parents=True)
    (destination / ".complete").write_text("ok\n", encoding="utf-8")
    (destination / "drift_test_dependency.py").write_text("VALUE = 42\n", encoding="utf-8")
    previous_pythonpath = os.environ.get("PYTHONPATH")
    previous_project_site = os.environ.get("DRIFT_PROJECT_SITE")
    try:
        assert python_environment_activate(project, tmp_path / ".drift", offline=True) == destination
        assert importlib.import_module("drift_test_dependency").VALUE == 42
        assert str(destination) in os.environ["DRIFT_PROJECT_SITE"]
    finally:
        sys.modules.pop("drift_test_dependency", None)
        if str(destination) in sys.path:
            sys.path.remove(str(destination))
        for name, value in (("PYTHONPATH", previous_pythonpath), ("DRIFT_PROJECT_SITE", previous_project_site)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_offline_project_python_environment_must_already_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIFT_HOME", str(tmp_path / "home"))
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("sample==1.0\n", encoding="utf-8")
    project = ProjectSpec("sample", python_requirements=(requirements,))

    with pytest.raises(ExecutionError, match="not materialized"):
        python_environment_activate(project, tmp_path / ".drift", offline=True)
