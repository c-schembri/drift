import json
import subprocess
import sys
from pathlib import Path


def test_action_expands_declared_paths(tmp_path: Path) -> None:
    output = tmp_path / "build" / "generated.txt"
    spec = tmp_path / "action.json"
    spec.write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('ok')",
                    "{out}",
                ],
                "environment": {},
                "timeout_seconds": 5,
                "outputs": [str(output)],
                "inputs": [],
                "root": str(tmp_path),
                "build_root": str(tmp_path / "build"),
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "driftbuild.action", "--spec", str(spec)], capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stderr
    assert output.read_text(encoding="utf-8") == "ok"
