import sys
from pathlib import Path

import pytest

from driftbuild.runtime import internal_dispatch, module_command, provider_command, script_command


def test_module_command_uses_python_module_when_installed() -> None:
    assert module_command("driftbuild.action") == (sys.executable, "-m", "driftbuild.action")


def test_module_command_reenters_frozen_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert module_command("driftbuild.action") == (
        sys.executable,
        "__drift_internal__",
        "driftbuild.action",
    )


def test_script_command_reenters_frozen_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "tool.py"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert script_command(script) == (
        sys.executable,
        "__drift_script__",
        str(script.resolve()),
    )


def test_internal_dispatch_rejects_arbitrary_modules() -> None:
    with pytest.raises(ValueError, match="Unsupported internal"):
        internal_dispatch(("__drift_internal__", "os"))


def test_provider_command_reenters_frozen_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert provider_command(tmp_path, "build:generate") == (
        sys.executable,
        "__drift_provider__",
        str(tmp_path.resolve()),
        "build:generate",
    )


def test_internal_dispatch_supports_python_code_reentry(capsys: pytest.CaptureFixture[str]) -> None:
    assert internal_dispatch(("-c", "print('embedded python')")) == 0
    assert capsys.readouterr().out == "embedded python\n"
