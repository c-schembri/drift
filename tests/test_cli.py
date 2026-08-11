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
    build_root = fixture / ".drift" / "build" / f"{sys.platform}-x86_64-{compiler}-debug"
    assert (build_root / "compile_commands.json").is_file()
    assert (build_root / "bin" / "message.txt").read_text(encoding="utf-8") == "hello from drift\n"

    artifact = subprocess.run(
        [*command, "artifact", "hello"], cwd=fixture, env=environment, capture_output=True, text=True, check=False
    )
    assert artifact.returncode == 0, artifact.stdout + artifact.stderr
    assert (fixture / ".drift" / "artifacts" / "hello.zip").is_file()

    generated = subprocess.run(
        [*command, "generate", "visual-studio", "--startup-target", "hello"],
        cwd=fixture,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    solution = fixture / ".drift" / "visual-studio" / "native-fixture.sln"
    assert solution.is_file()

    cleaned = subprocess.run(
        [*command, "clean", "hello"],
        cwd=fixture,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert cleaned.returncode == 0, cleaned.stdout + cleaned.stderr
    executable = build_root / "bin" / ("hello.exe" if os.name == "nt" else "hello")
    assert not executable.exists()

    if os.name == "nt":
        vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        located = subprocess.run(
            [str(vswhere), "-latest", "-products", "*", "-find", r"MSBuild\**\Bin\MSBuild.exe"],
            capture_output=True,
            text=True,
            check=True,
        )
        msbuild = located.stdout.splitlines()[0]
        for configuration in ("Debug", "Release"):
            built = subprocess.run(
                [
                    msbuild,
                    str(solution),
                    "/t:Build",
                    f"/p:Configuration={configuration}",
                    "/p:Platform=x64",
                    "/m",
                ],
                cwd=fixture,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            assert built.returncode == 0, built.stdout + built.stderr
            visual_studio_executable = (
                fixture / ".drift" / "build" / f"win32-x86_64-msvc-{configuration.casefold()}" / "bin" / "hello.exe"
            )
            assert visual_studio_executable.is_file()
