"""Drift command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import platform
import sys
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

from driftbuild import __version__
from driftbuild.errors import DriftError, ExecutionError
from driftbuild.graph import project_validate
from driftbuild.model import Artifact, BuildConfig, CommandContext, CommandResult, OptionSpec, ProjectSpec
from driftbuild.project import project_load, project_root_find


def _project_directory_normalize(arguments: list[str]) -> list[str]:
    if "--root" in arguments or any(value.startswith("--root=") for value in arguments):
        return arguments
    operations = {
        "artifact",
        "benchmark",
        "build",
        "clean",
        "configure",
        "generate",
        "graph",
        "task",
        "test",
    }
    operation_index = next((index for index, value in enumerate(arguments) if value in operations), None)
    if operation_index is None:
        return arguments
    candidate_index = operation_index + 1
    if arguments[operation_index] == "generate":
        candidate_index += 1
    if candidate_index >= len(arguments) or arguments[candidate_index].startswith("-"):
        return arguments
    candidate = Path(arguments[candidate_index]).resolve()
    if not candidate.is_dir() or not (candidate / "drift.toml").is_file():
        return arguments
    normalized = list(arguments)
    normalized.pop(candidate_index)
    return ["--root", str(candidate), *normalized]


def _configuration(arguments: argparse.Namespace) -> BuildConfig:
    values: dict[str, str] = {}
    for item in arguments.define:
        if "=" not in item:
            raise ExecutionError(f"Configuration value must be NAME=VALUE: {item}")
        name, value = item.split("=", 1)
        values[name] = value
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64"}.get(
        arguments.architecture.casefold(), arguments.architecture.casefold()
    )
    return BuildConfig(sys.platform, architecture, arguments.compiler, arguments.build_type, values)


def _root(arguments: argparse.Namespace) -> Path:
    return Path(arguments.root).resolve() if arguments.root else project_root_find(Path.cwd())


def _base_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drift", description="Typed Python project platform with a fast Ninja backend"
    )
    parser.add_argument("--version", action="version", version=f"drift {__version__}")
    parser.add_argument("--root", help="project root (otherwise discovered from the current directory)")
    parser.add_argument("--compiler", choices=("auto", "msvc", "gcc", "clang"), default="auto")
    parser.add_argument("--architecture", default=platform.machine().lower() or "x86_64")
    parser.add_argument("--build-type", choices=("debug", "release"), default="debug")
    parser.add_argument("-D", "--define", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("-v", "--verbose", action="store_true")
    commands = parser.add_subparsers(dest="operation", required=True)

    configure_parser = commands.add_parser("configure", help="validate the project and generate Ninja files")
    configure_parser.set_defaults(handler=_configure)
    build_parser = commands.add_parser("build", help="build default or named targets")
    build_parser.add_argument("targets", nargs="*")
    build_parser.set_defaults(handler=_build)
    clean_parser = commands.add_parser("clean", help="remove outputs for default or named targets")
    clean_parser.add_argument("targets", nargs="*")
    clean_parser.set_defaults(handler=_clean)
    graph_parser = commands.add_parser("graph", help="print the validated target graph as JSON")
    graph_parser.set_defaults(handler=_graph)
    task_parser = commands.add_parser("task", help="run workflow tasks")
    task_parser.add_argument("names", nargs="*")
    task_parser.add_argument("-j", "--jobs", type=int)
    task_parser.set_defaults(handler=_task)
    test_parser = commands.add_parser("test", help="build and run tests")
    test_parser.add_argument("names", nargs="*")
    test_parser.add_argument("--label", action="append", default=[])
    test_parser.add_argument("-j", "--jobs", type=int)
    test_parser.set_defaults(handler=_test)
    benchmark_parser = commands.add_parser("benchmark", help="run declared benchmarks")
    benchmark_parser.add_argument("names", nargs="*")
    benchmark_parser.set_defaults(handler=_benchmark)
    artifact_parser = commands.add_parser("artifact", help="build and create reproducible artifacts")
    artifact_parser.add_argument("names", nargs="*")
    artifact_parser.set_defaults(handler=_artifact)
    release_parser = commands.add_parser("release", help="validate or publish a declared release")
    release_parser.add_argument("name")
    release_parser.add_argument("--publish", action="store_true")
    release_parser.set_defaults(handler=_release)
    remote_parser = commands.add_parser("remote", help="execute an explicit command on a declared remote")
    remote_parser.add_argument("name")
    remote_parser.add_argument("command", nargs=argparse.REMAINDER)
    remote_parser.set_defaults(handler=_remote)
    provider_parser = commands.add_parser("command", help="run a provider-defined command")
    provider_parser.add_argument("arguments", nargs=argparse.REMAINDER)
    provider_parser.set_defaults(handler=_provider_command)
    generate_parser = commands.add_parser("generate", help="generate IDE integration files")
    generators = generate_parser.add_subparsers(dest="generator", required=True)
    visual_studio_parser = generators.add_parser("visual-studio", help="generate a Visual Studio solution")
    visual_studio_parser.add_argument("--output", type=Path)
    visual_studio_parser.add_argument("--startup-target")
    visual_studio_parser.set_defaults(handler=_generate_visual_studio)
    return parser


def _configure(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.build import configure

    result = configure(project, root, root / ".drift", config)
    print(result.generated.ninja_file)
    return 0


def _build(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.build import build

    build(project, root, root / ".drift", config, tuple(arguments.targets))
    return 0


def _clean(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.build import clean

    clean(project, root, root / ".drift", config, tuple(arguments.targets))
    return 0


def _generate_visual_studio(
    arguments: argparse.Namespace, _project: ProjectSpec, root: Path, config: BuildConfig
) -> int:
    from driftbuild.project import project_load
    from driftbuild.visual_studio import generate

    projects = {
        build_type: project_load(
            root,
            BuildConfig("win32", config.architecture, "msvc", build_type, config.values),
        )
        for build_type in ("debug", "release")
    }
    output_root = arguments.output.resolve() if arguments.output else root / ".drift" / "visual-studio"
    result = generate(
        projects,
        root,
        output_root,
        config.architecture,
        arguments.startup_target,
        config.values,
    )
    print(result.solution)
    return 0


def _graph(_arguments: argparse.Namespace, project: ProjectSpec, _root_path: Path, _config: BuildConfig) -> int:
    targets = project_validate(project)
    graph = {
        name: {
            "kind": target.kind,
            "dependencies": [
                *[item.target.name for item in target.dependencies if hasattr(item, "target")],
                *[item.name for item in target.objects],
            ],
            "outputs": [str(path) for path in target.outputs],
        }
        for name, target in targets.items()
    }
    print(json.dumps(graph, indent=2, sort_keys=True))
    return 0


def _task(arguments: argparse.Namespace, project: ProjectSpec, root: Path, _config: BuildConfig) -> int:
    from driftbuild.workflow import tasks_run

    for result in tasks_run(project, arguments.names, root, root / ".drift", arguments.jobs):
        print(f"{result.name}: {result.duration_seconds:.3f}s ({result.attempts} attempt(s))")
    return 0


def _test(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.testing import tests_run

    for result in tests_run(project, root, root / ".drift", config, arguments.names, arguments.label, arguments.jobs):
        print(f"PASS {result.name} ({result.duration_seconds:.3f}s)")
    return 0


def _benchmark(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.benchmark import benchmarks_run

    for result in benchmarks_run(project, root, root / ".drift", config, arguments.names):
        print(f"{result.name}: median {result.median_seconds:.6f}s, min {result.minimum_seconds:.6f}s")
    return 0


def _artifact_targets(project: ProjectSpec, names: list[str]) -> tuple[str, ...]:
    selected = [item for item in project.artifacts if not names or item.name in names]
    return tuple(
        sorted({value.target.name for item in selected for value in item.files if isinstance(value, Artifact)})
    )


def _artifact(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.artifact import artifacts_create
    from driftbuild.build import build

    names = list(arguments.names)
    result = build(project, root, root / ".drift", config, _artifact_targets(project, names))
    for path in artifacts_create(project, root, root / ".drift", result.generated.outputs, names):
        print(path)
    return 0


def _release(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.artifact import artifacts_create
    from driftbuild.build import build
    from driftbuild.release import release_publish

    release = next((item for item in project.releases if item.name == arguments.name), None)
    if release is None:
        raise ExecutionError(f"Unknown release: {arguments.name}")
    namespace = argparse.Namespace(names=list(release.artifacts))
    targets = _artifact_targets(project, namespace.names)
    result = build(project, root, root / ".drift", config, targets)
    paths = artifacts_create(project, root, root / ".drift", result.generated.outputs, namespace.names)
    print(release_publish(project, root, paths, arguments.name, publish=arguments.publish))
    return 0


def _remote(arguments: argparse.Namespace, project: ProjectSpec, _root_path: Path, _config: BuildConfig) -> int:
    from driftbuild.remote import remote_run

    remote = next((item for item in project.remotes if item.name == arguments.name), None)
    if remote is None:
        raise ExecutionError(f"Unknown remote: {arguments.name}")
    if not arguments.command:
        raise ExecutionError("Remote execution requires a command after --")
    result = remote_run(remote, arguments.command)
    print(result.stdout, end="")
    return result.returncode


def _option_add(parser: argparse.ArgumentParser, option: OptionSpec) -> None:
    name = "--" + option.name.replace("_", "-")
    keywords: dict[str, Any] = {"help": option.help, "dest": option.name}
    if option.flag:
        keywords["action"] = "store_true"
    else:
        keywords["type"] = option.value_type
        keywords["required"] = option.required
        if option.choices:
            keywords["choices"] = option.choices
    if option.default is not None:
        keywords["default"] = option.default
    parser.add_argument(name, **keywords)


def _provider_command(arguments: argparse.Namespace, project: ProjectSpec, root: Path, _config: BuildConfig) -> int:
    raw = list(arguments.arguments)
    command = next((item for item in project.commands if raw[: len(item.path)] == list(item.path)), None)
    if command is None:
        available = ", ".join(" ".join(item.path) for item in project.commands) or "none"
        raise ExecutionError(f"Unknown provider command. Available: {available}")
    parser = argparse.ArgumentParser(prog=f"drift command {' '.join(command.path)}", description=command.help)
    for option in command.options:
        _option_add(parser, option)
    parsed = parser.parse_args(raw[len(command.path) :])
    values = vars(parsed)
    options: Any = values
    if command.options_type is not None:
        if not is_dataclass(command.options_type):
            raise ExecutionError(f"Command {' '.join(command.path)} options_type must be a dataclass")
        options = command.options_type(**values)
    context = CommandContext(root, root / ".drift", dict(os.environ), arguments.verbose)
    result = command.handler(context, options)
    if inspect.isawaitable(result):
        result = asyncio.run(_await(result))
    if isinstance(result, CommandResult):
        if result.message:
            print(result.message)
        return result.exit_code
    return result if isinstance(result, int) else 0


async def _await(value: Any) -> Any:
    return await value


def main(argv: list[str] | None = None) -> int:
    """Run Drift and convert expected failures to concise diagnostics."""
    parser = _base_parser()
    try:
        raw_arguments = list(sys.argv[1:] if argv is None else argv)
        arguments = parser.parse_args(_project_directory_normalize(raw_arguments))
        root = _root(arguments)
        config = _configuration(arguments)
        project = project_load(root, config)
        project_validate(project)
        return int(arguments.handler(arguments, project, root, config))
    except DriftError as error:
        print(f"drift: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
