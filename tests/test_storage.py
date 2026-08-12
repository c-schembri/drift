from pathlib import Path

from driftbuild.storage import drift_home, tool_store_root


def test_drift_home_override_owns_shared_tools(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DRIFT_HOME", str(tmp_path / "shared"))

    assert drift_home() == (tmp_path / "shared").resolve()
    assert tool_store_root() == (tmp_path / "shared" / "tools").resolve()
