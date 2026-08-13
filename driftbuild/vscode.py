"""VS Code workspace generation backed by Drift commands."""

from __future__ import annotations

import json
from pathlib import Path

from driftbuild.configuration import config_key
from driftbuild.model import BuildConfig, ProjectSpec


def generate(project: ProjectSpec, root: Path, config: BuildConfig, output: Path | None = None) -> Path:
    """Generate tasks, launch configurations, and compile database settings."""
    directory = output or root / ".vscode"
    directory.mkdir(parents=True, exist_ok=True)
    tasks = {
        "version": "2.0.0",
        "tasks": [
            {
                "label": "drift: build",
                "type": "process",
                "command": "drift",
                "args": ["build"],
                "options": {"cwd": str(root)},
                "group": {"kind": "build", "isDefault": True},
                "problemMatcher": ["$msCompile" if config.platform == "win32" else "$gcc"],
            }
        ],
    }
    executables = [target for target in project.targets if target.kind == "executable" and not target.name.startswith("__")]
    launch = {
        "version": "0.2.0",
        "configurations": [
            {
                "name": f"Drift: {target.name}",
                "type": "cppvsdbg" if config.platform == "win32" else "cppdbg",
                "request": "launch",
                "program": str(
                    root
                    / ".drift"
                    / "build"
                    / config_key(config)
                    / "bin"
                    / (target.name + (".exe" if config.platform == "win32" else ""))
                ),
                "cwd": str(root),
                "preLaunchTask": "drift: build",
            }
            for target in executables
        ],
    }
    settings = {
        "C_Cpp.default.compileCommands": str(
            root / ".drift" / "build" / config_key(config) / "compile_commands.json"
        )
    }
    for name, payload in (("tasks.json", tasks), ("launch.json", launch), ("settings.json", settings)):
        (directory / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return directory
