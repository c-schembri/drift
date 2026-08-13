import sys

import pytest

from driftbuild.errors import ExecutionError
from driftbuild.process import command_render, run


def test_command_render_redacts_embedded_secrets() -> None:
    rendered = command_render(("tool", "--token=very-secret"), ("very-secret",))

    assert "very-secret" not in rendered
    assert "***" in rendered


def test_timeout_is_an_execution_error() -> None:
    with pytest.raises(ExecutionError, match="timed out"):
        run((sys.executable, "-c", "import time; time.sleep(2)"), timeout_seconds=0.01)


def test_run_forwards_child_output_by_default(capfd: pytest.CaptureFixture[str]) -> None:
    run(
        (
            sys.executable,
            "-c",
            "import sys; print('tool stdout', flush=True); print('tool stderr', file=sys.stderr, flush=True)",
        )
    )

    output = capfd.readouterr()
    assert output.out.splitlines() == ["tool stdout"]
    assert output.err.splitlines() == ["tool stderr"]


def test_run_only_hides_output_when_capture_is_requested(capfd: pytest.CaptureFixture[str]) -> None:
    result = run(
        (
            sys.executable,
            "-c",
            "import sys; print('machine stdout'); print('machine stderr', file=sys.stderr)",
        ),
        capture=True,
    )

    output = capfd.readouterr()
    assert output.out == ""
    assert output.err == ""
    assert result.stdout == "machine stdout\n"
    assert result.stderr == "machine stderr\n"
