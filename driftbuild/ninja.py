"""Deterministic lowering of Drift targets to Ninja."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.graph import project_validate, transitive_targets
from driftbuild.model import (
    Artifact,
    BuildConfig,
    BuildInput,
    Dependency,
    ProjectSpec,
    TargetDependency,
    TargetRef,
    TargetSpec,
)
from driftbuild.runtime import module_command
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
    if target.kind == "executable" and toolchain.family == "emscripten":
        return build_root / "bin" / f"{target.name}.js", build_root / "bin" / f"{target.name}.wasm"
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
        if config.warnings != "default":
            flags.append("/W4")
        if config.warnings == "error":
            flags.append("/WX")
        if config.lto:
            flags.append("/GL")
        if config.sanitizers:
            if config.sanitizers != ("address",):
                raise ConfigurationError("MSVC supports only --sanitize address")
            flags.append("/fsanitize=address")
        if config.coverage:
            raise ConfigurationError("Coverage instrumentation is not supported by the MSVC backend")
    else:
        flags = ["-D" + value for value in defines] + ["-I" + str(root / value) for value in includes]
        flags += ["-O0", "-g"] if config.build_type == "debug" else ["-O2", "-DNDEBUG"]
        if config.target is not None and toolchain.family == "clang" and config.profile != "android":
            flags.append(f"--target={config.target}")
        if config.profile == "ios" and toolchain.environment.get("SDKROOT"):
            flags.append(f"-isysroot={toolchain.environment['SDKROOT']}")
        if config.sysroot is not None:
            flags.append(f"--sysroot={config.sysroot}")
        if target.kind == "shared_library":
            flags.append("-fPIC")
        if config.warnings != "default":
            flags.extend(("-Wall", "-Wextra"))
        if config.warnings == "error":
            flags.append("-Werror")
        if config.sanitizers:
            flags.append("-fsanitize=" + ",".join(config.sanitizers))
        if config.coverage:
            flags.append("--coverage")
        if config.lto:
            flags.append("-flto")
    return [*flags, *arguments]


def _unity_sources(target: TargetSpec, root: Path, build_root: Path, group_size: int) -> tuple[BuildInput, ...]:
    if group_size == 0:
        return target.sources
    passthrough: list[BuildInput] = []
    groups: dict[str, list[Path]] = {"c": [], "cpp": []}
    for source in target.sources:
        if not isinstance(source, Path):
            passthrough.append(source)
            continue
        suffix = source.suffix.casefold()
        language = "c" if suffix == ".c" else "cpp" if suffix in (".cc", ".cpp", ".cxx") else None
        if language is None:
            passthrough.append(source)
        else:
            groups[language].append(source)
    unity_root = build_root / "unity" / target.name
    for language, sources in groups.items():
        for index in range(0, len(sources), group_size):
            chunk = sources[index : index + group_size]
            if len(chunk) == 1:
                passthrough.append(chunk[0])
                continue
            output = unity_root / f"{index // group_size}.{language}"
            content = "".join(f'#include "{(root / source).resolve().as_posix()}"\n' for source in chunk)
            _write_if_changed(output, content)
            passthrough.append(output.resolve())
    return tuple(passthrough)


def _dependency_action_outputs(
    target: TargetSpec,
    targets: Mapping[str, TargetSpec],
    outputs: Mapping[str, tuple[Path, ...]],
) -> list[Path]:
    result: list[Path] = []
    visited: set[str] = set()

    def add(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        dependency = targets[name]
        if dependency.action is not None:
            result.extend(outputs[name])
        for nested in dependency.dependencies:
            if isinstance(nested, TargetDependency):
                add(_dependency_target_name(nested))

    for dependency in target.dependencies:
        if isinstance(dependency, TargetDependency):
            add(_dependency_target_name(dependency))
    return list(dict.fromkeys(result))


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

    def link_library(path: Path) -> bool:
        if toolchain.family == "msvc":
            return path.suffix.casefold() in (".lib", ".a")
        name = path.name.casefold()
        return path.suffix.casefold() in (".a", ".so", ".dylib") or ".so." in name

    def library_argument(value: str | Path) -> str:
        return str(root / value) if isinstance(value, Path) else value

    def add_target(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        child = targets[name]
        if child.kind in ("static_library", "shared_library", "external_library"):
            arguments.extend(child.link_arguments)
            candidates = outputs[child.name]
            if toolchain.family == "msvc" and child.kind == "shared_library":
                paths.extend(path for path in candidates if path.suffix == ".lib")
            elif child.kind == "external_library":
                libraries = tuple(path for path in candidates if link_library(path))
                paths.extend(libraries)
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
        target_sources = _unity_sources(target, root, build_root, config.unity_size)
        pch_output: Path | None = None
        pch_object: Path | None = None
        pch_include: tuple[str, ...] = ()
        if target.precompiled_header is not None:
            header = (root / target.precompiled_header).resolve()
            languages = {
                "c" if _source_path(root, source, outputs).suffix.casefold() in (".c", ".m") else "c++"
                for source in target_sources
                if _source_path(root, source, outputs).suffix.casefold() in (".c", ".cc", ".cpp", ".cxx", ".m", ".mm")
            }
            if len(languages) > 1:
                raise ConfigurationError(f"Target {target.name} cannot use one precompiled header for mixed C and C++")
            language = next(iter(languages), "c++")
            pch_root = build_root / "pch" / target.name
            pch_root.mkdir(parents=True, exist_ok=True)
            pch_flags = _compile_flags(target, targets, root, config, toolchain)
            pch_outputs: tuple[Path, ...]
            compiler = toolchain.cc if language == "c" else toolchain.cxx
            wrapper = pch_root / ("drift_pch.c" if language == "c" else "drift_pch.cpp")
            _write_if_changed(wrapper, f'#include "{header.as_posix()}"\n')
            if toolchain.family == "msvc":
                pch_output = pch_root / "drift.pch"
                pch_object = pch_root / f"drift{toolchain.object_suffix}"
                pch_include = (f"/Yu{header}", f"/Fp{pch_output}", f"/FI{header}")
                pch_arguments = [
                    compiler,
                    "/nologo",
                    "/showIncludes",
                    "/c",
                    "/TC" if language == "c" else "/TP",
                    str(wrapper),
                    f"/Yc{header}",
                    f"/Fp{pch_output}",
                    f"/Fo{pch_object}",
                    f"/FI{header}",
                    *pch_flags,
                ]
                pch_outputs = (pch_output, pch_object)
            elif toolchain.family == "clang":
                pch_output = pch_root / "drift.pch"
                pch_include = ("-include-pch", str(pch_output))
                pch_arguments = [
                    compiler,
                    "-MMD",
                    f"-MF{pch_output}.d",
                    "-x",
                    f"{language}-header",
                    str(wrapper),
                    "-o",
                    str(pch_output),
                    *pch_flags,
                ]
                pch_outputs = (pch_output,)
            elif toolchain.family == "gcc":
                forwarding = pch_root / "drift_pch.h"
                _write_if_changed(forwarding, f'#include "{header.as_posix()}"\n')
                pch_output = forwarding.with_suffix(".h.gch")
                pch_include = ("-I" + str(pch_root), "-include", forwarding.name)
                pch_arguments = [
                    compiler,
                    "-MMD",
                    f"-MF{pch_output}.d",
                    "-x",
                    f"{language}-header",
                    str(forwarding),
                    "-o",
                    str(pch_output),
                    *pch_flags,
                ]
                pch_outputs = (pch_output,)
            else:
                raise ConfigurationError(f"Precompiled headers are not supported by {toolchain.family}")
            lines += [
                f"build {' '.join(_ninja(value) for value in pch_outputs)}: cc {_ninja(header)} {_ninja(wrapper)}",
                f"  command = {_shell(pch_arguments)}",
                "",
            ]
            output_phases.update((value, "compile") for value in pch_outputs)
            if pch_object is not None:
                objects.append(pch_object)
        for index, source in enumerate(target_sources):
            source_path = _source_path(root, source, outputs)
            if source_path.suffix.lower() not in (".c", ".cc", ".cpp", ".cxx", ".m", ".mm"):
                continue
            object_path = build_root / "obj" / target.name / f"{index}-{source_path.stem}{toolchain.object_suffix}"
            object_path.parent.mkdir(parents=True, exist_ok=True)
            compiler = toolchain.cc if source_path.suffix.lower() in (".c", ".m") else toolchain.cxx
            flags = _compile_flags(target, targets, root, config, toolchain)
            flags.extend(pch_include)
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
            order_inputs = _dependency_action_outputs(target, targets, outputs)
            if pch_output is not None:
                order_inputs.append(pch_output)
            order_text = f" || {' '.join(_ninja(path) for path in order_inputs)}" if order_inputs else ""
            lines += [
                f"build {_ninja(object_path)}: cc {_ninja(source_path)}{order_text}",
                f"  command = {command}",
                "",
            ]
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
            runner = _shell([*module_command("driftbuild.action"), "--spec", str(spec_path)])
            input_text = " ".join(_ninja(path) for path in inputs)
            implicit_inputs.append(spec_path)
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
            runner = _shell([*module_command("driftbuild.bundle"), "--spec", str(spec_path)])
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
        if toolchain.family == "msvc":
            if config.lto:
                link_arguments.append("/LTCG")
            if config.sanitizers:
                link_arguments.append("/fsanitize=address")
        else:
            if config.sanitizers:
                link_arguments.append("-fsanitize=" + ",".join(config.sanitizers))
            if config.coverage:
                link_arguments.append("--coverage")
            if config.lto:
                link_arguments.append("-flto")
        inputs = [*object_outputs[target.name], *link_paths]
        order_inputs = [path for path in _dependency_action_outputs(target, targets, outputs) if path not in inputs]
        order_text = f" || {' '.join(_ninja(path) for path in order_inputs)}" if order_inputs else ""
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
            cross_arguments = []
            if config.target is not None and toolchain.family == "clang" and config.profile != "android":
                cross_arguments.append(f"--target={config.target}")
            if config.profile == "ios" and toolchain.environment.get("SDKROOT"):
                cross_arguments.append(f"-isysroot={toolchain.environment['SDKROOT']}")
            if config.sysroot is not None:
                cross_arguments.append(f"--sysroot={config.sysroot}")
            arguments = [
                toolchain.linker,
                *cross_arguments,
                "-o",
                str(output),
                *map(str, inputs),
                *link_arguments,
            ]
            if target.kind == "shared_library":
                arguments.append("-shared")
            rule = "link"
        output_phases.update((path, rule) for path in outputs[target.name])
        command_variable = "tool_command" if toolchain.family == "msvc" else "command"
        lines += [
            f"build {' '.join(_ninja(path) for path in outputs[target.name])}: {rule} "
            f"{' '.join(_ninja(path) for path in inputs)}{order_text}",
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
