"""Deterministic lowering of Drift targets to Ninja."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.graph import project_validate, transitive_targets
from driftbuild.model import Artifact, BuildConfig, Dependency, ProjectSpec, TargetDependency, TargetRef, TargetSpec
from driftbuild.toolchain import Toolchain


@dataclass(frozen=True)
class GeneratedBuild:
    """Generated backend files and target output mapping."""

    ninja_file: Path
    compilation_database: Path
    outputs: Mapping[str, tuple[Path, ...]]
    output_phases: Mapping[Path, str]


def _ninja(value: str | Path) -> str:
    return str(value).replace("$", "$$").replace(" ", "$ ").replace(":", "$:")


def _shell(arguments: Sequence[str]) -> str:
    if os.name == "nt":
        rendered = subprocess.list2cmdline(list(arguments))
    else:
        rendered = shlex.join(arguments)
    return rendered.replace("$", "$$")


def _write_if_changed(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    if path.is_file() and path.read_bytes() == encoded:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)


def _dependency_target_name(dependency: TargetDependency) -> str:
    assert isinstance(dependency.target, TargetRef)
    return dependency.target.name


def _target_output(target: TargetSpec, build_root: Path, toolchain: Toolchain) -> tuple[Path, ...]:
    if target.outputs:
        return tuple(build_root / output for output in target.outputs)
    if target.kind == "object_library":
        return ()
    if target.kind == "static_library":
        return (build_root / "lib" / f"{toolchain.static_prefix}{target.name}{toolchain.static_suffix}",)
    if target.kind == "shared_library":
        runtime = build_root / "bin" / f"{toolchain.shared_prefix}{target.name}{toolchain.shared_suffix}"
        if toolchain.family == "msvc":
            return (runtime, build_root / "lib" / f"{target.name}.lib")
        return (runtime,)
    if target.kind == "executable":
        return (build_root / "bin" / f"{target.name}{toolchain.executable_suffix}",)
    return ()


def _source_path(root: Path, value: Path | Artifact, outputs: Mapping[str, tuple[Path, ...]]) -> Path:
    if isinstance(value, Path):
        return root / value
    produced = outputs[value.target.name]
    selected = next((path for path in produced if path.name == value.path.name or path == value.path), None)
    if selected is None and value.path == Path(value.target.name) and produced:
        selected = produced[0]
    if selected is None:
        raise ConfigurationError(f"Target {value.target.name} does not produce {value.path.as_posix()}")
    return selected


def _public_interface(
    targets: Mapping[str, TargetSpec], name: str, seen: set[str] | None = None
) -> tuple[list[Path], list[str], list[str]]:
    visited = set() if seen is None else seen
    if name in visited:
        return [], [], []
    visited.add(name)
    target = targets[name]
    includes = list(target.include_dirs)
    defines = list(target.defines)
    arguments = list(target.compile_arguments)
    for dependency in target.dependencies:
        if isinstance(dependency, Dependency):
            includes.extend(dependency.compile.include_dirs)
            defines.extend(dependency.compile.defines)
            arguments.extend(dependency.compile.arguments)
        elif dependency.visibility == "public":
            child = _public_interface(targets, _dependency_target_name(dependency), visited)
            includes.extend(child[0])
            defines.extend(child[1])
            arguments.extend(child[2])
    return includes, defines, arguments


def _compile_flags(
    target: TargetSpec,
    targets: Mapping[str, TargetSpec],
    root: Path,
    config: BuildConfig,
    toolchain: Toolchain,
) -> list[str]:
    includes = list(target.include_dirs)
    defines = list(target.defines)
    arguments = list(target.compile_arguments)
    for dependency in target.dependencies:
        if isinstance(dependency, Dependency):
            includes.extend(dependency.compile.include_dirs)
            defines.extend(dependency.compile.defines)
            arguments.extend(dependency.compile.arguments)
        else:
            child = _public_interface(targets, _dependency_target_name(dependency))
            includes.extend(child[0])
            defines.extend(child[1])
            arguments.extend(child[2])
    if toolchain.family == "msvc":
        flags = ["/D" + value for value in defines] + ["/I" + str(root / value) for value in includes]
        flags += ["/Od", "/Z7"] if config.build_type == "debug" else ["/O2", "/DNDEBUG"]
    else:
        flags = ["-D" + value for value in defines] + ["-I" + str(root / value) for value in includes]
        flags += ["-O0", "-g"] if config.build_type == "debug" else ["-O2", "-DNDEBUG"]
        if target.kind == "shared_library":
            flags.append("-fPIC")
    return [*flags, *arguments]


def _link_inputs(
    target: TargetSpec,
    targets: Mapping[str, TargetSpec],
    outputs: Mapping[str, tuple[Path, ...]],
    root: Path,
    toolchain: Toolchain,
) -> tuple[list[Path], list[str]]:
    paths: list[Path] = []
    arguments = list(target.link_arguments)
    visited: set[str] = set()

    def library_argument(value: str | Path) -> str:
        return str(root / value) if isinstance(value, Path) else value

    def add_target(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        child = targets[name]
        if child.kind in ("static_library", "shared_library", "external_library"):
            candidates = outputs[child.name]
            if toolchain.family == "msvc" and child.kind == "shared_library":
                paths.extend(path for path in candidates if path.suffix == ".lib")
            elif child.kind == "external_library":
                suffixes = {".lib"} if toolchain.family == "msvc" else {".a", ".so"}
                libraries = tuple(path for path in candidates if path.suffix in suffixes)
                paths.extend(libraries or candidates)
            else:
                paths.extend(candidates)
        for nested in child.dependencies:
            if isinstance(nested, Dependency):
                arguments.extend(library_argument(value) for value in nested.link.libraries)
                arguments.extend(nested.link.arguments)
                prefix = "/LIBPATH:" if toolchain.family == "msvc" else "-L"
                arguments.extend(f"{prefix}{root / value}" for value in nested.link.library_dirs)
            elif nested.visibility == "public":
                add_target(_dependency_target_name(nested))

    for dependency in target.dependencies:
        if isinstance(dependency, Dependency):
            arguments.extend(library_argument(value) for value in dependency.link.libraries)
            arguments.extend(dependency.link.arguments)
            prefix = "/LIBPATH:" if toolchain.family == "msvc" else "-L"
            arguments.extend(f"{prefix}{root / value}" for value in dependency.link.library_dirs)
        else:
            add_target(_dependency_target_name(dependency))
    for reference in target.objects:
        paths.extend(outputs[reference.name])
    return paths, arguments


def generate(
    project: ProjectSpec, root: Path, build_root: Path, config: BuildConfig, toolchain: Toolchain
) -> GeneratedBuild:
    """Validate and generate build.ninja plus compile_commands.json."""
    targets = project_validate(project)
    outputs = {name: _target_output(target, build_root, toolchain) for name, target in targets.items()}
    lines = ["# Generated by Drift. Do not edit.", "ninja_required_version = 1.10", ""]
    for pool in project.pools:
        lines += [f"pool {pool.name}", f"  depth = {pool.depth}", ""]
    if toolchain.family == "msvc":
        lines += ["rule cc", "  command = $command", "  deps = msvc", "  description = CC $out", ""]
    else:
        lines += [
            "rule cc",
            "  command = $command",
            "  depfile = $out.d",
            "  deps = gcc",
            "  description = CC $out",
            "",
        ]
    if toolchain.family == "msvc":
        lines += [
            "rule archive",
            '  command = $tool_command "@$response_file"',
            "  rspfile = $response_file",
            "  rspfile_content = $in",
            "  description = AR $out",
            "",
            "rule link",
            '  command = $tool_command "@$response_file"',
            "  rspfile = $response_file",
            "  rspfile_content = $in $link_arguments",
            "  description = LINK $out",
            "",
        ]
    else:
        lines += [
            "rule archive",
            "  command = $command",
            "  description = AR $out",
            "",
            "rule link",
            "  command = $command",
            "  description = LINK $out",
            "",
        ]
    lines += [
        "rule action",
        "  command = $command",
        "  description = $description",
        "  restat = $restat",
        "",
        "rule action_gcc",
        "  command = $command",
        "  description = $description",
        "  depfile = $depfile",
        "  deps = gcc",
        "  restat = $restat",
        "",
        "rule action_msvc",
        "  command = $command",
        "  description = $description",
        "  deps = msvc",
        "  restat = $restat",
        "",
    ]
    compdb: list[dict[str, str]] = []
    object_outputs: dict[str, list[Path]] = {}
    output_phases: dict[Path, str] = {}
    action_root = build_root / "actions"

    for target in project.targets:
        objects: list[Path] = []
        for index, source in enumerate(target.sources):
            source_path = _source_path(root, source, outputs)
            if source_path.suffix.lower() not in (".c", ".cc", ".cpp", ".cxx", ".m", ".mm"):
                continue
            object_path = build_root / "obj" / target.name / f"{index}-{source_path.stem}{toolchain.object_suffix}"
            object_path.parent.mkdir(parents=True, exist_ok=True)
            compiler = toolchain.cc if source_path.suffix.lower() in (".c", ".m") else toolchain.cxx
            flags = _compile_flags(target, targets, root, config, toolchain)
            if toolchain.family == "msvc":
                arguments = [compiler, "/nologo", "/showIncludes", "/c", str(source_path), f"/Fo{object_path}", *flags]
            else:
                arguments = [
                    compiler,
                    "-MMD",
                    f"-MF{object_path}.d",
                    "-c",
                    str(source_path),
                    "-o",
                    str(object_path),
                    *flags,
                ]
            command = _shell(arguments)
            lines += [f"build {_ninja(object_path)}: cc {_ninja(source_path)}", f"  command = {command}", ""]
            objects.append(object_path)
            output_phases[object_path] = "compile"
            compdb.append(
                {"directory": str(root), "file": str(source_path), "output": str(object_path), "command": command}
            )
        object_outputs[target.name] = objects

        if target.kind in ("custom", "external_library"):
            assert target.action is not None
            action_outputs = outputs[target.name]
            inputs = [_source_path(root, value, outputs) for value in target.action.inputs]
            implicit_inputs = [_source_path(root, value, outputs) for value in target.action.implicit_inputs]
            order_only = [_source_path(root, value, outputs) for value in target.action.order_only]
            payload: dict[str, object] = {
                "command": list(target.action.command),
                "environment": dict(target.action.environment),
                "timeout_seconds": target.action.timeout_seconds,
                "outputs": [str(path) for path in action_outputs],
                "inputs": [str(path) for path in inputs],
                "root": str(root),
                "build_root": str(build_root),
            }
            spec_path = action_root / f"{target.name}.json"
            _write_if_changed(spec_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            runner = _shell([sys.executable, "-m", "driftbuild.action", "--spec", str(spec_path)])
            input_text = " ".join(_ninja(path) for path in inputs)
            implicit_text = f" | {' '.join(_ninja(path) for path in implicit_inputs)}" if implicit_inputs else ""
            order_text = f" || {' '.join(_ninja(path) for path in order_only)}" if order_only else ""
            rule = "action" if target.action.deps is None else f"action_{target.action.deps}"
            lines += [
                f"build {' '.join(_ninja(path) for path in action_outputs)}: {rule} {input_text}{implicit_text}{order_text}".rstrip(),
                f"  command = {runner}",
                f"  description = {target.action.description or 'ACTION ' + target.name}",
                f"  restat = {1 if target.action.restat else 0}",
                "",
            ]
            if target.action.depfile is not None:
                lines.insert(-1, f"  depfile = {_ninja(build_root / target.action.depfile)}")
            if target.action.pool is not None:
                lines.insert(-1, f"  pool = {target.action.pool}")
            output_phases.update((path, "action") for path in action_outputs)

        if target.kind == "runtime_bundle":
            bundle_files = [_source_path(root, value, outputs) for value in target.runtime_files]
            output = outputs[target.name][0]
            destination = output.parent
            payload = {
                "files": [str(path) for path in bundle_files],
                "destination": str(destination),
                "stamp": str(output),
            }
            spec_path = action_root / f"{target.name}-bundle.json"
            _write_if_changed(spec_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            runner = _shell([sys.executable, "-m", "driftbuild.bundle", "--spec", str(spec_path)])
            lines += [
                f"build {_ninja(output)}: action {' '.join(_ninja(path) for path in bundle_files)}",
                f"  command = {runner}",
                f"  description = BUNDLE {target.name}",
                "  restat = 1",
                "",
            ]
            output_phases[output] = "action"

    for target in project.targets:
        if target.kind == "object_library":
            outputs[target.name] = tuple(object_outputs[target.name])

    for target in project.targets:
        if target.kind == "object_library":
            continue
        if target.kind not in ("static_library", "shared_library", "executable"):
            continue
        link_paths, link_arguments = _link_inputs(target, targets, outputs, root, toolchain)
        inputs = [*object_outputs[target.name], *link_paths]
        output = outputs[target.name][0]
        output.parent.mkdir(parents=True, exist_ok=True)
        if target.kind == "static_library":
            arguments = (
                [toolchain.archiver, "/NOLOGO", f"/OUT:{output}"]
                if toolchain.family == "msvc"
                else [toolchain.archiver, "rcs", str(output), *map(str, inputs)]
            )
            rule = "archive"
        elif toolchain.family == "msvc":
            arguments = [toolchain.linker, "/NOLOGO", f"/OUT:{output}"]
            if target.kind == "shared_library":
                arguments.append("/DLL")
                arguments.append(f"/IMPLIB:{outputs[target.name][1]}")
            rule = "link"
        else:
            arguments = [toolchain.linker, "-o", str(output), *map(str, inputs), *link_arguments]
            if target.kind == "shared_library":
                arguments.append("-shared")
            rule = "link"
        output_phases.update((path, rule) for path in outputs[target.name])
        command_variable = "tool_command" if toolchain.family == "msvc" else "command"
        lines += [
            f"build {' '.join(_ninja(path) for path in outputs[target.name])}: {rule} {' '.join(_ninja(path) for path in inputs)}",
            f"  {command_variable} = {_shell(arguments)}",
        ]
        if toolchain.family == "msvc":
            response_file = build_root / "rsp" / f"{target.name}-{rule}.rsp"
            response_file.parent.mkdir(parents=True, exist_ok=True)
            lines += [
                f"  response_file = {_ninja(response_file)}",
                f"  link_arguments = {_shell(link_arguments) if rule == 'link' else ''}",
            ]
        lines.append("")

    for target in project.targets:
        target_outputs = outputs[target.name]
        if target.kind == "alias":
            dependencies = [path for reference in target.objects for path in outputs[reference.name]]
            lines += [f"build {target.name}: phony {' '.join(_ninja(path) for path in dependencies)}", ""]
        elif target_outputs:
            lines += [f"build {target.name}: phony {' '.join(_ninja(path) for path in target_outputs)}", ""]
    defaults = [reference.name for reference in project.defaults]
    if defaults:
        transitive_targets(targets, defaults)
        lines += [f"default {' '.join(defaults)}", ""]

    ninja_file = build_root / "build.ninja"
    compilation_database = build_root / "compile_commands.json"
    _write_if_changed(ninja_file, "\n".join(lines))
    _write_if_changed(compilation_database, json.dumps(compdb, indent=2) + "\n")
    return GeneratedBuild(ninja_file, compilation_database, outputs, output_phases)
