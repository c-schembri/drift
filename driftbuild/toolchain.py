"""Host C and C++ compiler discovery."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.model import BuildConfig


@dataclass(frozen=True)
class Toolchain:
    """Resolved compiler tools, environment, and platform naming policy."""

    family: str
    cc: str
    cxx: str
    linker: str
    archiver: str
    environment: Mapping[str, str]
    object_suffix: str
    executable_suffix: str
    static_prefix: str
    static_suffix: str
    shared_prefix: str
    shared_suffix: str


def _vc_environment(architecture: str, state_root: Path | None) -> dict[str, str]:
    candidates: list[Path] = []
    vswhere = (
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
        / "Microsoft Visual Studio/Installer/vswhere.exe"
    )
    if vswhere.is_file():
        discovered = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if discovered.returncode == 0 and discovered.stdout.strip():
            candidates.append(Path(discovered.stdout.strip()) / "VC/Auxiliary/Build/vcvarsall.bat")
    candidates.extend(
        [
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Microsoft Visual Studio/2022/Community/VC/Auxiliary/Build/vcvarsall.bat",
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Microsoft Visual Studio/2022/Professional/VC/Auxiliary/Build/vcvarsall.bat",
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Microsoft Visual Studio/2022/Enterprise/VC/Auxiliary/Build/vcvarsall.bat",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
            / "Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvarsall.bat",
        ]
    )
    script = next((path for path in candidates if path.is_file()), None)
    if script is None:
        raise ConfigurationError("MSVC requested but a Visual Studio C++ toolchain was not found")
    argument = {"x86_64": "x64", "x86": "x86", "arm64": "arm64"}.get(architecture)
    if argument is None:
        raise ConfigurationError(f"Unsupported MSVC architecture: {architecture}")
    cache = state_root / "toolchains" / f"msvc-{architecture}.json" if state_root is not None else None
    if cache is not None and cache.is_file():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            if payload["script"] == str(script) and payload["mtime_ns"] == script.stat().st_mtime_ns:
                environment = payload["environment"]
                if isinstance(environment, dict) and all(
                    isinstance(name, str) and isinstance(value, str) for name, value in environment.items()
                ):
                    return environment
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
    completed = subprocess.run(
        f'call "{script}" {argument} >nul && set',
        capture_output=True,
        text=True,
        check=False,
        shell=True,
    )
    if completed.returncode != 0:
        raise ConfigurationError(f"MSVC environment setup failed: {completed.stderr.strip()}")
    environment = dict(os.environ)
    for line in completed.stdout.splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            environment[name] = value
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {"script": str(script), "mtime_ns": script.stat().st_mtime_ns, "environment": environment},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return environment


def toolchain_resolve(config: BuildConfig, state_root: Path | None = None) -> Toolchain:
    """Resolve the selected host compiler or fail with an actionable error."""
    if config.toolchain_file is not None and config.toolchain_file.suffix.casefold() == ".json":
        try:
            payload = json.loads(config.toolchain_file.read_text(encoding="utf-8"))
            required = ("family", "cc", "cxx", "linker", "archiver")
            if not isinstance(payload, dict) or any(not isinstance(payload.get(name), str) for name in required):
                raise ValueError(f"required string fields are: {', '.join(required)}")
            environment = dict(os.environ)
            raw_environment = payload.get("environment", {})
            if not isinstance(raw_environment, dict) or any(
                not isinstance(name, str) or not isinstance(value, str) for name, value in raw_environment.items()
            ):
                raise ValueError("environment must be an object of string values")
            environment.update(raw_environment)
            return Toolchain(
                payload["family"],
                payload["cc"],
                payload["cxx"],
                payload["linker"],
                payload["archiver"],
                environment,
                str(payload.get("object_suffix", ".obj" if payload["family"] == "msvc" else ".o")),
                str(payload.get("executable_suffix", ".exe" if config.platform == "win32" else "")),
                str(payload.get("static_prefix", "" if payload["family"] == "msvc" else "lib")),
                str(payload.get("static_suffix", ".lib" if payload["family"] == "msvc" else ".a")),
                str(payload.get("shared_prefix", "" if config.platform == "win32" else "lib")),
                str(
                    payload.get(
                        "shared_suffix",
                        ".dll" if config.platform == "win32" else ".dylib" if config.platform == "darwin" else ".so",
                    )
                ),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ConfigurationError(f"Invalid Drift toolchain file {config.toolchain_file}: {error}") from error
    family = config.compiler
    if family == "auto":
        family = "clang" if config.target is not None else "msvc" if os.name == "nt" else "gcc"
    if family == "msvc":
        if os.name != "nt":
            raise ConfigurationError("MSVC is only supported on Windows")
        environment = _vc_environment(config.architecture, state_root)
        return Toolchain("msvc", "cl", "cl", "link", "lib", environment, ".obj", ".exe", "", ".lib", "", ".dll")
    if family not in ("gcc", "clang"):
        raise ConfigurationError(f"Unsupported compiler: {family}")
    cc = "gcc" if family == "gcc" else "clang"
    cxx = "g++" if family == "gcc" else "clang++"
    missing = [tool for tool in (cc, cxx, "ar") if shutil.which(tool) is None]
    if missing:
        raise ConfigurationError(f"{family} toolchain is incomplete; missing: {', '.join(missing)}")
    executable_suffix = ".exe" if os.name == "nt" else ""
    shared_suffix = ".dll" if os.name == "nt" else ".dylib" if config.platform == "darwin" else ".so"
    return Toolchain(
        family,
        cc,
        cxx,
        cxx,
        "ar",
        dict(os.environ),
        ".o",
        executable_suffix,
        "lib",
        ".a",
        "" if os.name == "nt" else "lib",
        shared_suffix,
    )
