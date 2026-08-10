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
