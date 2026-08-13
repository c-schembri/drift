"""Cargo execution bridge with stable, discovered Drift artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _metadata_inputs(payload: object) -> tuple[Path, ...]:
    if not isinstance(payload, dict):
        return ()
    found: set[Path] = set()
    workspace_root_value = payload.get("workspace_root")
    if isinstance(workspace_root_value, str):
        workspace_root = Path(workspace_root_value)
        for relative_name in ("Cargo.lock", ".cargo/config", ".cargo/config.toml"):
            candidate = workspace_root / relative_name
            if candidate.is_file():
                found.add(candidate.resolve())
    packages = payload.get("packages", [])
    if not isinstance(packages, list):
        return tuple(sorted(found))
    for package in packages:
        if not isinstance(package, dict) or package.get("source") is not None:
            continue
        manifest_value = package.get("manifest_path")
        if not isinstance(manifest_value, str):
            continue
        manifest = Path(manifest_value).resolve()
        found.add(manifest)
        package_root = manifest.parent
        for candidate_path in package_root.rglob("*"):
            if not candidate_path.is_file() or candidate_path.is_symlink():
                continue
            try:
                relative_path = candidate_path.relative_to(package_root)
            except ValueError:
                continue
            if any(part in (".git", ".drift", "target") for part in relative_path.parts):
                continue
            if candidate_path.suffix == ".rs" or candidate_path.name in ("Cargo.toml", "Cargo.lock", "build.rs"):
                found.add(candidate_path.resolve())
    return tuple(sorted(found, key=lambda path: path.as_posix().casefold()))


def _depfile_write(path: Path, target: Path, inputs: tuple[Path, ...]) -> None:
    def escape(value: Path) -> str:
        return str(value.resolve()).replace("\\", "/").replace(":", "\\:").replace(" ", "\\ ")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(f"{escape(target)}: {' '.join(escape(value) for value in inputs)}\n", encoding="utf-8")
    os.replace(temporary, path)


def _same_file(source: Path, destination: Path) -> bool:
    if not destination.is_file():
        return False
    source_stat = source.stat()
    destination_stat = destination.stat()
    return source_stat.st_size == destination_stat.st_size and source_stat.st_mtime_ns == destination_stat.st_mtime_ns


def _artifact_find(messages: list[dict[str, Any]], kind: str, name: str) -> Path | None:
    normalized = name.replace("-", "_")
    for message in reversed(messages):
        if message.get("reason") != "compiler-artifact":
            continue
        target = message.get("target", {})
        target_name = str(target.get("name", ""))
        if target_name not in (name, normalized) and target_name.replace("-", "_") != normalized:
            continue
        target_kinds = target.get("kind", [])
        if kind == "bin":
            executable = message.get("executable")
            if "bin" in target_kinds and isinstance(executable, str):
                return Path(executable)
            continue
        if kind == "staticlib" and "staticlib" not in target_kinds:
            continue
        filenames = message.get("filenames", [])
        candidates = [Path(value) for value in filenames if isinstance(value, str)]
        expected = {f"{normalized}.lib".casefold(), f"lib{normalized}.a".casefold()}
        static = next((path for path in candidates if path.name.casefold() in expected), None)
        if static is None:
            static = next(
                (
                    path
                    for path in candidates
                    if path.suffix.casefold() in (".a", ".lib")
                    and not path.name.casefold().endswith(".dll.lib")
                ),
                None,
            )
        if static is not None:
            return static
    return None


def main() -> int:
    """Run Cargo once, discover compiler artifacts, and publish stable outputs."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--depfile", type=Path, required=True)
    parser.add_argument("--dep-target", type=Path, required=True)
    parser.add_argument("--workspace", action="store_true")
    parser.add_argument("--package", action="append", default=[])
    parser.add_argument("--bin", action="append", default=[])
    parser.add_argument("--features")
    parser.add_argument("--all-features", action="store_true")
    parser.add_argument("--no-default-features", action="store_true")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--output", action="append", type=Path, default=[])
    parser.add_argument("cargo_arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if len(arguments.artifact) != len(arguments.output):
        parser.error("--artifact and --output counts must match")

    metadata_command = [
        "cargo",
        "metadata",
        "--format-version=1",
        "--manifest-path",
        str(arguments.manifest),
    ]
    metadata = subprocess.run(
        metadata_command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if metadata.returncode != 0:
        print(metadata.stderr, end="", file=sys.stderr)
        return metadata.returncode
    try:
        metadata_payload: object = json.loads(metadata.stdout)
    except json.JSONDecodeError as error:
        print(f"drift cargo: invalid cargo metadata output: {error}", file=sys.stderr)
        return 2

    command = [
        "cargo",
        "build",
        "--manifest-path",
        str(arguments.manifest),
        "--target-dir",
        str(arguments.target_dir),
        "--message-format=json-render-diagnostics",
    ]
    if arguments.release:
        command.append("--release")
    if arguments.workspace:
        command.append("--workspace")
    for package in arguments.package:
        command.extend(("--package", package))
    for binary in arguments.bin:
        command.extend(("--bin", binary))
    if arguments.features:
        command.extend(("--features", arguments.features))
    if arguments.all_features:
        command.append("--all-features")
    if arguments.no_default_features:
        command.append("--no-default-features")
    command.extend(arguments.cargo_arguments[1:] if arguments.cargo_arguments[:1] == ["--"] else arguments.cargo_arguments)

    process = subprocess.Popen(
        command,
        cwd=Path.cwd(),
        env=os.environ,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    messages: list[dict[str, Any]] = []
    assert process.stdout is not None
    for line in process.stdout:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print(line, end="")
            continue
        if isinstance(message, dict):
            messages.append(message)
            rendered = message.get("message", {}).get("rendered")
            if isinstance(rendered, str):
                print(rendered, end="", file=sys.stderr)
    return_code = process.wait()
    if return_code != 0:
        return return_code

    for selector, output in zip(arguments.artifact, arguments.output, strict=True):
        kind, separator, name = selector.partition(":")
        if not separator or kind not in ("bin", "staticlib") or not name:
            print(f"drift cargo: invalid artifact selector: {selector}", file=sys.stderr)
            return 2
        source = _artifact_find(messages, kind, name)
        if source is None or not source.is_file():
            print(f"drift cargo: Cargo did not produce {selector}", file=sys.stderr)
            return 2
        output.parent.mkdir(parents=True, exist_ok=True)
        if not _same_file(source, output):
            temporary = output.with_suffix(output.suffix + ".tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, output)
    _depfile_write(arguments.depfile, arguments.dep_target, _metadata_inputs(metadata_payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
