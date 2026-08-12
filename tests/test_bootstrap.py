from pathlib import Path

from driftbuild import bootstrap


def test_host_key_normalizes_apple_silicon(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "arm64")

    assert bootstrap._host_key() == ("darwin", "arm64")
    assert bootstrap._NINJA_ARCHIVES[bootstrap._host_key()].name == "ninja-mac.zip"


def test_cmake_override_avoids_bootstrap(tmp_path: Path) -> None:
    executable = tmp_path / "cmake"
    executable.touch()

    assert bootstrap.cmake_resolve(tmp_path / "state", str(executable)) == executable.resolve()


def test_meson_override_avoids_bootstrap(tmp_path: Path) -> None:
    executable = tmp_path / "meson"
    executable.touch()

    assert bootstrap.meson_command(tmp_path / "state", str(executable)) == (str(executable.resolve()),)
