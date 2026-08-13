"""Host C and C++ compiler discovery."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
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
    if config.profile == "emscripten":
        missing = [tool for tool in ("emcc", "em++", "emar") if shutil.which(tool) is None]
        if missing:
            raise ConfigurationError("Emscripten profile requires an activated emsdk; missing: " + ", ".join(missing))
        return Toolchain("emscripten", "emcc", "em++", "em++", "emar", dict(os.environ), ".o", ".js", "lib", ".a", "lib", ".wasm")
    if config.profile == "android":
        ndk_value = os.environ.get("ANDROID_NDK_ROOT") or os.environ.get("ANDROID_NDK_HOME")
        if ndk_value is None:
            raise ConfigurationError("Android profile requires ANDROID_NDK_ROOT")
        host = "windows-x86_64" if os.name == "nt" else "darwin-x86_64" if sys.platform == "darwin" else "linux-x86_64"
        bin_root = Path(ndk_value) / "toolchains/llvm/prebuilt" / host / "bin"
        api = config.values.get("android_api", "24")
        triple = "aarch64-linux-android" if config.architecture == "arm64" else "x86_64-linux-android"
        suffix = ".cmd" if os.name == "nt" else ""
        android_cc = bin_root / f"{triple}{api}-clang{suffix}"
        android_cxx = bin_root / f"{triple}{api}-clang++{suffix}"
        android_ar = bin_root / ("llvm-ar.exe" if os.name == "nt" else "llvm-ar")
        if not all(path.is_file() for path in (android_cc, android_cxx, android_ar)):
            raise ConfigurationError(f"Android NDK toolchain is incomplete under {bin_root}")
        return Toolchain("clang", str(android_cc), str(android_cxx), str(android_cxx), str(android_ar), dict(os.environ), ".o", "", "lib", ".a", "lib", ".so")
    if config.profile == "ios":
        if platform.system() != "Darwin":
            raise ConfigurationError("iOS profile requires a macOS host with Xcode")
        tools = {}
        for name in ("clang", "clang++", "ar"):
            result = subprocess.run(("xcrun", "--sdk", "iphoneos", "--find", name), capture_output=True, text=True)
            if result.returncode != 0:
                raise ConfigurationError(f"iOS profile could not locate {name} through xcrun")
            tools[name] = result.stdout.strip()
        environment = dict(os.environ)
        sdk = subprocess.run(("xcrun", "--sdk", "iphoneos", "--show-sdk-path"), capture_output=True, text=True)
        if sdk.returncode == 0:
            environment["SDKROOT"] = sdk.stdout.strip()
        return Toolchain("clang", tools["clang"], tools["clang++"], tools["clang++"], tools["ar"], environment, ".o", "", "lib", ".a", "lib", ".dylib")
    if family == "mingw":
        prefix = f"{config.target or 'x86_64-w64-mingw32'}-"
        mingw_cc, mingw_cxx, mingw_ar = prefix + "gcc", prefix + "g++", prefix + "ar"
        missing = [tool for tool in (mingw_cc, mingw_cxx, mingw_ar) if shutil.which(tool) is None]
        if missing:
            raise ConfigurationError("MinGW profile is incomplete; missing: " + ", ".join(missing))
        return Toolchain("gcc", mingw_cc, mingw_cxx, mingw_cxx, mingw_ar, dict(os.environ), ".o", ".exe", "lib", ".a", "", ".dll")
    if family in ("msvc", "clang-cl"):
        if os.name != "nt":
            raise ConfigurationError(f"{family} is only supported on Windows")
        environment = _vc_environment(config.architecture, state_root)
        if family == "clang-cl":
            missing = [tool for tool in ("clang-cl", "lld-link", "llvm-lib") if shutil.which(tool, path=environment.get("PATH")) is None]
            if missing:
                raise ConfigurationError("clang-cl profile is incomplete; missing: " + ", ".join(missing))
            return Toolchain("msvc", "clang-cl", "clang-cl", "lld-link", "llvm-lib", environment, ".obj", ".exe", "", ".lib", "", ".dll")
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
