import os
import subprocess
import sys
from pathlib import Path


def test_native_fixture_build_test_and_compdb() -> None:
    repository = Path(__file__).parents[1]
    fixture = repository / "examples" / "native"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repository)
    compiler = os.environ.get("DRIFT_TEST_COMPILER", "auto")
    command = [sys.executable, "-m", "driftbuild", "--root", str(fixture), "--compiler", compiler]

    build = subprocess.run(
        [*command, "build"], cwd=fixture, env=environment, capture_output=True, text=True, check=False
    )
    assert build.returncode == 0, build.stdout + build.stderr
    test = subprocess.run(
        [*command, "test", "hello"], cwd=fixture, env=environment, capture_output=True, text=True, check=False
    )
    assert test.returncode == 0, test.stdout + test.stderr
    assert "PASS hello" in test.stdout
    build_root = next((fixture / ".drift" / "build").iterdir())
    assert (build_root / "compile_commands.json").is_file()
    assert (build_root / "bin" / "message.txt").read_text(encoding="utf-8") == "hello from drift\n"

    artifact = subprocess.run(
        [*command, "artifact", "hello"], cwd=fixture, env=environment, capture_output=True, text=True, check=False
    )
    assert artifact.returncode == 0, artifact.stdout + artifact.stderr
    assert (fixture / ".drift" / "artifacts" / "hello.zip").is_file()
