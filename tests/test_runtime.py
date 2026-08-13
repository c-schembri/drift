import sys
from pathlib import Path

import pytest

from driftbuild.runtime import internal_dispatch, module_command, script_command


def test_module_command_uses_python_module_when_installed() -> None:
    assert module_command("driftbuild.action") == (str(Path(sys.executable).resolve()), "-m", "driftbuild.action")


def test_module_command_reenters_frozen_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert module_command("driftbuild.action") == (
        str(Path(sys.executable).resolve()),
        "__drift_internal__",
        "driftbuild.action",
    )


def test_script_command_reenters_frozen_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "tool.py"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert script_command(script) == (
        str(Path(sys.executable).resolve()),
        "__drift_script__",
        str(script.resolve()),
    )


def test_internal_dispatch_rejects_arbitrary_modules() -> None:
    with pytest.raises(ValueError, match="Unsupported internal"):
        internal_dispatch(("__drift_internal__", "os"))
