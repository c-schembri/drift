"""Execute project-owned handlers through the installed Drift runtime."""

from __future__ import annotations

import argparse
import importlib
import inspect
import sys
from collections.abc import Sequence
from pathlib import Path

from driftbuild.errors import ExecutionError


def invoke(root: Path, handler: str, arguments: Sequence[str]) -> int:
    """Import and invoke one `module:function` handler from a project root."""
    module_name, separator, function_name = handler.partition(":")
    if not separator:
        raise ExecutionError(f"Invalid provider handler: {handler}")
    sys.path.insert(0, str(root.resolve()))
    try:
        function = getattr(importlib.import_module(module_name), function_name)
        result = function(tuple(arguments))
    except (ImportError, AttributeError, TypeError) as error:
        raise ExecutionError(f"Cannot invoke provider handler {handler}: {error}") from error
    finally:
        sys.path.pop(0)
    if inspect.isawaitable(result):
        raise ExecutionError(f"Provider action {handler} must be synchronous")
    return result if isinstance(result, int) else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--handler", required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    values = parser.parse_args()
    return invoke(values.root, values.handler, values.arguments)


if __name__ == "__main__":
    raise SystemExit(main())
