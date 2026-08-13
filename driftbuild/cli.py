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
from typing import Any, cast

from driftbuild import __version__
from driftbuild.errors import DriftError, ExecutionError
from driftbuild.graph import project_validate
from driftbuild.model import (
    Artifact,
    BuildConfig,
    CommandContext,
    CommandResult,
    OptionSpec,
    PackageTargetRef,
    ProjectSpec,
    TargetDependency,
)
from driftbuild.project import project_load, project_root_find


def _project_directory_normalize(arguments: list[str]) -> list[str]:
    if "--root" in arguments or any(value.startswith("--root=") for value in arguments):
        return arguments
    operations = {
        "artifact",
        "benchmark",
        "build",
        "cache",
        "clean",
        "configure",
        "doctor",
        "fetch",
        "generate",
        "graph",
        "inspect",
        "install",
        "lock",
        "outdated",
        "perf",
        "run",
        "task",
        "targets",
        "test",
        "update",
    }
    operation_index = next((index for index, value in enumerate(arguments) if value in operations), None)
    if operation_index is None:
        return arguments
    first_candidate = operation_index + (2 if arguments[operation_index] == "generate" else 1)
    candidate_index = next(
        (
            index
            for index in range(first_candidate, len(arguments))
            if not arguments[index].startswith("-")
            and Path(arguments[index]).is_dir()
            and (Path(arguments[index]) / "drift.toml").is_file()
        ),
        None,
    )
    if candidate_index is None:
        return arguments
    candidate = Path(arguments[candidate_index]).resolve()
    normalized = list(arguments)
    normalized.pop(candidate_index)
    return ["--root", str(candidate), *normalized]


def _configuration(arguments: argparse.Namespace, root: Path) -> BuildConfig:
    values: dict[str, str] = {}
    for item in arguments.define:
        if "=" not in item:
            raise ExecutionError(f"Configuration value must be NAME=VALUE: {item}")
        name, value = item.split("=", 1)
        values[name] = value
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64"}.get(
        arguments.architecture.casefold(), arguments.architecture.casefold()
    )
    profile = arguments.profile
    if arguments.unity is not None and arguments.unity < 2:
        raise ExecutionError("--unity requires a group size of at least 2")
    if "thread" in arguments.sanitize and len(arguments.sanitize) > 1:
        raise ExecutionError("thread sanitizer cannot be combined with another sanitizer")
    selected_compiler = arguments.compiler
    if selected_compiler == "auto" and profile in ("mingw", "clang-cl"):
        selected_compiler = profile
    elif selected_compiler == "auto" and profile in ("android", "ios", "emscripten"):
        selected_compiler = "clang"
    target = arguments.target
    profile_targets = {
        ("android", "arm64"): "aarch64-linux-android",
        ("android", "x86_64"): "x86_64-linux-android",
        ("ios", "arm64"): "arm64-apple-ios",
        ("emscripten", "x86_64"): "wasm32-unknown-emscripten",
        ("mingw", "x86_64"): "x86_64-w64-mingw32",
    }
    if target is None:
        target = profile_targets.get((profile, architecture))
    target_key = target.casefold() if target is not None else ""
    if target is not None:
        architecture = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64"}.get(
            target.split("-", 1)[0].casefold(), target.split("-", 1)[0].casefold()
        )
    selected_platform = (
        "emscripten"
        if profile == "emscripten"
        else "linux"
        if profile == "android"
        else "darwin"
        if profile == "ios"
        else
        "win32"
        if "windows" in target_key or "mingw" in target_key
        else "darwin"
        if "darwin" in target_key or "apple" in target_key
        else "linux"
        if "linux" in target_key
        else sys.platform
    )

    def selected_path(value: str | None, label: str) -> Path | None:
        if value is None:
            return None
        path = Path(value)
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
        if not resolved.exists():
            raise ExecutionError(f"{label} does not exist: {resolved}")
        return resolved

    return BuildConfig(
        selected_platform,
        architecture,
        selected_compiler,
        arguments.build_type,
        values,
        target,
        selected_path(arguments.sysroot, "Sysroot"),
        selected_path(arguments.toolchain, "Toolchain file"),
        tuple(dict.fromkeys(arguments.sanitize)),
        arguments.coverage,
        arguments.lto,
        arguments.warnings,
        arguments.unity or 0,
        profile,
    )


def _root(arguments: argparse.Namespace) -> Path:
    return Path(arguments.root).resolve() if arguments.root else project_root_find(Path.cwd())


def _base_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drift", description="Typed Python project platform with a fast Ninja backend"
    )
    parser.add_argument("--version", action="version", version=f"drift {__version__}")
    parser.add_argument("--root", help="project root (otherwise discovered from the current directory)")
    parser.add_argument(
        "--compiler", choices=("auto", "msvc", "gcc", "clang", "mingw", "clang-cl"), default="auto"
    )
    parser.add_argument("--architecture", default=platform.machine().lower() or "x86_64")
    parser.add_argument("--build-type", choices=("debug", "release"), default="debug")
    parser.add_argument("--target", help="target triple for cross compilation")
    parser.add_argument("--sysroot", help="target sysroot directory")
    parser.add_argument("--toolchain", help="Drift JSON or upstream toolchain file")
    parser.add_argument(
        "--profile", choices=("host", "android", "ios", "emscripten", "mingw", "clang-cl"), default="host"
    )
    parser.add_argument("--sanitize", action="append", choices=("address", "undefined", "thread"), default=[])
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--lto", action="store_true")
    parser.add_argument("--warnings", choices=("default", "all", "error"), default="default")
    parser.add_argument("--unity", type=int, metavar="FILES", help="combine source files in groups of this size")
    parser.add_argument("-D", "--define", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--offline", action="store_true", help="forbid package network access")
    parser.add_argument("-v", "--verbose", action="store_true")
    commands = parser.add_subparsers(dest="operation", required=True)

    configure_parser = commands.add_parser("configure", help="validate the project and generate Ninja files")
    configure_parser.set_defaults(handler=_configure)
    build_parser = commands.add_parser("build", help="build default or named targets")
    build_parser.add_argument("targets", nargs="*")
    build_parser.add_argument("-j", "--jobs", type=int)
    build_parser.add_argument("--explain", action="store_true", help="explain why Ninja rebuilds each edge")
    build_parser.add_argument("--keep-going", action="store_true", help="continue independent work after failures")
    build_parser.add_argument("--dry-run", action="store_true", help="show pending work without executing it")
    build_parser.set_defaults(handler=_build)
    doctor_parser = commands.add_parser("doctor", help="check the active project and build environment")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(handler=_doctor)
    cache_parser = commands.add_parser("cache", help="inspect or clean shared Drift caches")
    cache_commands = cache_parser.add_subparsers(dest="cache_operation", required=True)
    cache_path_parser = cache_commands.add_parser("path", help="print the shared cache root")
    cache_path_parser.set_defaults(handler=_cache)
    cache_status_parser = cache_commands.add_parser("status", help="show cache sizes")
    cache_status_parser.add_argument("--json", action="store_true")
    cache_status_parser.set_defaults(handler=_cache)
    cache_clean_parser = cache_commands.add_parser("clean", help="remove selected shared cache categories")
    cache_clean_parser.add_argument(
        "categories", nargs="+", choices=("sources", "binaries", "tools", "conan", "vcpkg", "all")
    )
    cache_clean_parser.add_argument("--yes", action="store_true", help="confirm shared cache deletion")
    cache_clean_parser.set_defaults(handler=_cache)
    cache_export_parser = cache_commands.add_parser("export", help="export shared caches to an archive")
    cache_export_parser.add_argument("archive", type=Path)
    cache_export_parser.add_argument(
        "categories", nargs="*", default=["all"], choices=("sources", "binaries", "tools", "conan", "vcpkg", "all")
    )
    cache_export_parser.set_defaults(handler=_cache)
    cache_import_parser = cache_commands.add_parser("import", help="import a shared cache archive")
    cache_import_parser.add_argument("archive", type=Path)
    cache_import_parser.add_argument("--replace", action="store_true")
    cache_import_parser.set_defaults(handler=_cache)
    cache_pull_parser = cache_commands.add_parser("pull", help="download and import a remote cache archive")
    cache_pull_parser.add_argument("url")
    cache_pull_parser.add_argument("--replace", action="store_true")
    cache_pull_parser.set_defaults(handler=_cache)
    cache_push_parser = cache_commands.add_parser("push", help="export and upload shared caches")
    cache_push_parser.add_argument("url")
    cache_push_parser.add_argument(
        "categories", nargs="*", default=["all"], choices=("sources", "binaries", "tools", "conan", "vcpkg", "all")
    )
    cache_push_parser.set_defaults(handler=_cache)
    clean_parser = commands.add_parser("clean", help="remove outputs for default or named targets")
    clean_parser.add_argument("targets", nargs="*")
    clean_parser.set_defaults(handler=_clean)
    lock_parser = commands.add_parser("lock", help="resolve exact package sources and replace drift.lock")
    lock_parser.add_argument("--check", action="store_true", help="fail if drift.lock would change")
    lock_parser.add_argument("--diff", action="store_true", help="print lock changes")
    lock_parser.add_argument("--refresh", action="store_true", help="rematerialize exact declared sources")
    lock_parser.set_defaults(handler=_lock)
    outdated_parser = commands.add_parser("outdated", help="report whether package declarations changed since lock")
    outdated_parser.set_defaults(handler=_outdated)
    update_parser = commands.add_parser("update", help="refresh exact package sources and rewrite drift.lock")
    update_parser.set_defaults(handler=_update)
    fetch_parser = commands.add_parser("fetch", help="download and verify packages from drift.lock")
    fetch_parser.set_defaults(handler=_fetch)
    inspect_parser = commands.add_parser("inspect", help="show resolved package adapters, inputs, and outputs")
    inspect_parser.add_argument("names", nargs="*")
    inspect_parser.set_defaults(handler=_inspect)
    install_parser = commands.add_parser("install", help="build and install a conventional SDK layout")
    install_parser.add_argument("targets", nargs="*")
    install_parser.add_argument("--prefix", type=Path, required=True)
    install_parser.set_defaults(handler=_install)
    run_parser = commands.add_parser("run", help="build and run an executable target")
    run_parser.add_argument("arguments", nargs=argparse.REMAINDER)
    run_parser.set_defaults(handler=_run)
    graph_parser = commands.add_parser("graph", help="print the validated target graph as JSON")
    graph_parser.set_defaults(handler=_graph)
    targets_parser = commands.add_parser("targets", help="list declared build targets")
    targets_parser.add_argument("--all", action="store_true", help="include internal package targets")
    targets_parser.add_argument("--json", action="store_true")
    targets_parser.set_defaults(handler=_targets)
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
    perf_parser = commands.add_parser("perf", help="measure Drift configure and no-op build overhead")
    perf_parser.add_argument("--repetitions", type=int, default=5)
    perf_parser.add_argument("--output", type=Path)
    perf_parser.add_argument("--json", action="store_true")
    perf_parser.set_defaults(handler=_performance)
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
    vscode_parser = generators.add_parser("vscode", help="generate VS Code tasks and launch settings")
    vscode_parser.add_argument("--output", type=Path)
    vscode_parser.set_defaults(handler=_generate_vscode)
    xcode_parser = generators.add_parser("xcode", help="generate an Xcode legacy project")
    xcode_parser.add_argument("--output", type=Path)
    xcode_parser.set_defaults(handler=_generate_xcode)
    standalone_parser = commands.add_parser("standalone", help="create a portable Drift zipapp")
    standalone_parser.add_argument("--output", type=Path, default=Path("drift.pyz"))
    standalone_parser.set_defaults(handler=_standalone)
    return parser


def _configure(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.build import configure

    result = configure(project, root, root / ".drift", config)
    print(result.generated.ninja_file)
    return 0


def _build(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.build import build, build_timing_render

    result = build(
        project,
        root,
        root / ".drift",
        config,
        tuple(arguments.targets),
        jobs=arguments.jobs,
        verbose=arguments.verbose,
        explain=arguments.explain,
        keep_going=arguments.keep_going,
        dry_run=arguments.dry_run,
    )
    assert result.timing is not None
    print(build_timing_render(result.timing))
    return 0


def _clean(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.build import clean

    clean(project, root, root / ".drift", config, tuple(arguments.targets))
    return 0


def _lock(arguments: argparse.Namespace, project: ProjectSpec, root: Path, _config: BuildConfig) -> int:
    from driftbuild.packages import package_lock_create, package_lock_diff, package_lock_read

    previous = package_lock_read(root) if (root / "drift.lock").is_file() else None
    result = package_lock_create(project, root, refresh=arguments.refresh, write=not arguments.check)
    changes = package_lock_diff(previous, result)
    if arguments.diff or arguments.check:
        print("\n".join(changes) if changes else "drift.lock is current")
    if arguments.check:
        return 1 if changes else 0
    print(f"Locked {len(result.packages)} package(s) in {root / 'drift.lock'}")
    return 0


def _outdated(_arguments: argparse.Namespace, project: ProjectSpec, root: Path, _config: BuildConfig) -> int:
    from driftbuild.packages import package_lock_create, package_lock_diff, package_lock_read

    previous = package_lock_read(root) if (root / "drift.lock").is_file() else None
    candidate = package_lock_create(project, root, refresh=True, write=False)
    changes = package_lock_diff(previous, candidate)
    print("\n".join(changes) if changes else "All packages are locked at their declared revisions")
    return 1 if changes else 0


def _update(_arguments: argparse.Namespace, project: ProjectSpec, root: Path, _config: BuildConfig) -> int:
    from driftbuild.packages import package_lock_create, package_lock_diff, package_lock_read

    previous = package_lock_read(root) if (root / "drift.lock").is_file() else None
    updated = package_lock_create(project, root, refresh=True)
    changes = package_lock_diff(previous, updated)
    print("\n".join(changes) if changes else "Package sources verified; drift.lock is unchanged")
    return 0


def _fetch(arguments: argparse.Namespace, project: ProjectSpec, root: Path, _config: BuildConfig) -> int:
    from driftbuild.packages import packages_fetch

    roots = packages_fetch(project, root, offline=arguments.offline)
    print(f"Fetched and verified {len(roots)} package(s)")
    return 0


def _doctor(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.doctor import doctor_run

    result = doctor_run(project, root, config)
    if arguments.json:
        print(json.dumps(result, indent=2))
    else:
        for check in result["checks"]:
            print(f"{str(check['status']).upper():7} {check['name']}: {check['detail']}")
    return 0 if result["ok"] else 1


def _cache(arguments: argparse.Namespace, _project: ProjectSpec, _root: Path, _config: BuildConfig) -> int:
    from driftbuild.cache import (
        cache_clean,
        cache_export,
        cache_import,
        cache_pull,
        cache_push,
        cache_status,
        size_render,
    )
    from driftbuild.storage import drift_home

    if arguments.cache_operation == "path":
        print(drift_home())
        return 0
    if arguments.cache_operation == "clean":
        entries = cache_clean(tuple(arguments.categories), confirmed=arguments.yes)
        for entry in entries:
            print(f"Removed {entry.name}: {size_render(entry.bytes)} in {entry.files} file(s)")
        return 0
    if arguments.cache_operation == "export":
        print(cache_export(arguments.archive.resolve(), tuple(arguments.categories)))
        return 0
    if arguments.cache_operation == "import":
        print(f"Imported {cache_import(arguments.archive.resolve(), replace=arguments.replace)} file(s)")
        return 0
    if arguments.cache_operation == "pull":
        print(f"Imported {cache_pull(arguments.url, replace=arguments.replace)} file(s)")
        return 0
    if arguments.cache_operation == "push":
        cache_push(arguments.url, tuple(arguments.categories))
        print(f"Pushed cache to {arguments.url}")
        return 0
    entries = cache_status()
    if arguments.json:
        print(
            json.dumps(
                [
                    {"name": entry.name, "path": str(entry.path), "bytes": entry.bytes, "files": entry.files}
                    for entry in entries
                ],
                indent=2,
            )
        )
    else:
        for entry in entries:
            print(f"{entry.name:10} {size_render(entry.bytes):>10}  {entry.files:>7} files  {entry.path}")
        print(f"total      {size_render(sum(entry.bytes for entry in entries)):>10}")
    return 0


def _inspect(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.inspection import packages_inspect

    print(json.dumps(packages_inspect(project, root, config, tuple(arguments.names), arguments.offline), indent=2))
    return 0


def _install(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.build import build
    from driftbuild.install import project_install

    result = build(project, root, root / ".drift", config, tuple(arguments.targets))
    manifest = project_install(
        project,
        root,
        arguments.prefix.resolve(),
        dict(result.generated.outputs),
        tuple(arguments.targets),
    )
    print(manifest)
    return 0


def _run(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.runner import build_and_run

    values = list(arguments.arguments)
    separator = values.index("--") if "--" in values else len(values)
    selectors = values[:separator]
    program_arguments = values[separator + 1 :] if separator < len(values) else ()
    if len(selectors) > 1:
        raise ExecutionError("Run accepts at most one target before --")
    target = selectors[0] if selectors else None
    return build_and_run(project, root, root / ".drift", config, target, program_arguments)


def _generate_visual_studio(
    arguments: argparse.Namespace, _project: ProjectSpec, root: Path, config: BuildConfig
) -> int:
    from driftbuild.packages import packages_compose
    from driftbuild.project import project_load
    from driftbuild.visual_studio import generate

    projects: dict[str, ProjectSpec] = {}
    for build_type in ("debug", "release"):
        selected = BuildConfig("win32", config.architecture, "msvc", build_type, config.values)
        loaded = project_load(root, selected)
        projects[build_type] = packages_compose(loaded, root, selected, offline=arguments.offline)
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


def _generate_vscode(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.vscode import generate

    print(generate(project, root, config, arguments.output.resolve() if arguments.output else None))
    return 0


def _generate_xcode(arguments: argparse.Namespace, project: ProjectSpec, root: Path, _config: BuildConfig) -> int:
    from driftbuild.xcode import generate

    print(generate(project, root, arguments.output.resolve() if arguments.output else None))
    return 0


def _standalone(arguments: argparse.Namespace, _project: ProjectSpec, _root: Path, _config: BuildConfig) -> int:
    from driftbuild.distribution import standalone_create

    print(standalone_create(arguments.output.resolve()))
    return 0


def _graph(_arguments: argparse.Namespace, project: ProjectSpec, _root_path: Path, _config: BuildConfig) -> int:
    targets = project_validate(project)
    graph = {
        name: {
            "kind": target.kind,
            "dependencies": [
                *[
                    f"@{item.target.package}//{item.target.target}"
                    if isinstance(item.target, PackageTargetRef)
                    else item.target.name
                    for item in target.dependencies
                    if isinstance(item, TargetDependency)
                ],
                *[item.name for item in target.objects],
            ],
            "outputs": [str(path) for path in target.outputs],
        }
        for name, target in targets.items()
    }
    print(json.dumps(graph, indent=2, sort_keys=True))
    return 0


def _targets(arguments: argparse.Namespace, project: ProjectSpec, _root: Path, _config: BuildConfig) -> int:
    defaults = {reference.name for reference in project.defaults}
    targets = [target for target in project.targets if arguments.all or not target.name.startswith("__drift_package_")]
    if arguments.json:
        print(
            json.dumps(
                [{"name": target.name, "kind": target.kind, "default": target.name in defaults} for target in targets],
                indent=2,
            )
        )
    else:
        for target in targets:
            marker = "*" if target.name in defaults else " "
            print(f"{marker} {target.name:24} {target.kind}")
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


def _performance(arguments: argparse.Namespace, project: ProjectSpec, root: Path, config: BuildConfig) -> int:
    from driftbuild.performance import performance_run

    payload = performance_run(project, root, root / ".drift", config, arguments.repetitions, arguments.output)
    if arguments.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        configure_payload = cast(dict[str, float], payload["configure"])
        no_op_payload = cast(dict[str, float], payload["no_op_build"])
        configure_median = configure_payload["median_seconds"]
        no_op_median = no_op_payload["median_seconds"]
        print(f"Drift performance ({arguments.repetitions} runs):")
        print(f"  warm configure median: {configure_median:.3f}s")
        print(f"  no-op build median:    {no_op_median:.3f}s")
        print(f"  report: {payload['report']}")
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
        projectless = arguments.operation in ("cache", "standalone")
        root = (
            (Path(arguments.root).resolve() if arguments.root else Path.cwd().resolve())
            if projectless
            else _root(arguments)
        )
        config = _configuration(arguments, root)
        project = ProjectSpec("drift-cache") if projectless else project_load(root, config)
        if arguments.operation not in (
            "lock",
            "fetch",
            "inspect",
            "outdated",
            "update",
            "doctor",
            "cache",
            "standalone",
        ):
            from driftbuild.packages import packages_compose

            project = packages_compose(project, root, config, offline=arguments.offline)
        project_validate(project)
        return int(arguments.handler(arguments, project, root, config))
    except DriftError as error:
        print(f"drift: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
