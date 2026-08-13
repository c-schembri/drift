"""Deterministic dependency-aware workflow execution."""

from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from driftbuild.errors import ExecutionError
from driftbuild.model import CommandContext, ProjectSpec, TaskSpec
from driftbuild.process import OwnedProcess, command_render


@dataclass(frozen=True)
class TaskResult:
    """One workflow task outcome."""

    name: str
    attempts: int
    duration_seconds: float
    status: str = "passed"
    detail: str = ""


class _ProcessRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: set[OwnedProcess] = set()

    def add(self, process: OwnedProcess) -> None:
        with self._lock:
            self._processes.add(process)

    def remove(self, process: OwnedProcess) -> None:
        with self._lock:
            self._processes.discard(process)

    def stop_all(self) -> None:
        with self._lock:
            processes = tuple(self._processes)
        for process in processes:
            process.stop()


async def _await(value: Awaitable[Any]) -> Any:
    return await value


def _invoke(handler: Callable[..., Any], context: CommandContext) -> None:
    value = handler(context)
    if inspect.isawaitable(value):
        asyncio.run(_await(value))


def _execute(task: TaskSpec, context: CommandContext, registry: _ProcessRegistry) -> TaskResult:
    started = time.perf_counter()
    last_error: Exception | None = None
    for attempt in range(1, task.retries + 2):
        try:
            if task.command is not None:
                environment = dict(context.environment)
                environment.update(task.environment)
                process = OwnedProcess(
                    task.command,
                    cwd=context.project_root,
                    environment=environment,
                )
                registry.add(process)
                try:
                    try:
                        return_code = process.wait(timeout=task.timeout_seconds)
                    except subprocess.TimeoutExpired as error:
                        raise ExecutionError(f"Command timed out: {command_render(task.command)}") from error
                    if return_code != 0:
                        raise ExecutionError(f"Command failed ({return_code}): {command_render(task.command)}")
                finally:
                    try:
                        process.stop()
                    finally:
                        registry.remove(process)
            elif task.handler is not None:
                _invoke(task.handler, context)
            return TaskResult(task.name, attempt, time.perf_counter() - started)
        except Exception as error:
            last_error = error
    assert last_error is not None
    return TaskResult(
        task.name,
        task.retries + 1,
        time.perf_counter() - started,
        "failed",
        str(last_error),
    )


def tasks_run(
    project: ProjectSpec, names: Sequence[str], root: Path, state_root: Path, jobs: int | None = None
) -> tuple[TaskResult, ...]:
    """Run requested tasks and dependencies, respecting named resource locks."""
    tasks = {task.name: task for task in project.tasks}
    requested = tuple(names) or tuple(sorted(tasks))
    required: set[str] = set()

    def include(name: str) -> None:
        if name not in tasks:
            raise ExecutionError(f"Unknown task: {name}")
        if name in required:
            return
        required.add(name)
        for dependency in tasks[name].dependencies:
            include(dependency)

    for name in requested:
        include(name)
    context = CommandContext(root, state_root, dict(os.environ))
    pending = set(required)
    complete: set[str] = set()
    failed: set[str] = set()
    running: dict[Future[TaskResult], TaskSpec] = {}
    held_resources: set[str] = set()
    results: list[TaskResult] = []
    registry = _ProcessRegistry()
    worker_count = jobs or max(1, min(32, os.cpu_count() or 1))
    executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="drift-task")
    try:
        while pending or running:
            launched = False
            for name in sorted(pending):
                task = tasks[name]
                failed_dependencies = sorted(set(task.dependencies) & failed)
                if failed_dependencies:
                    pending.remove(name)
                    failed.add(name)
                    results.append(
                        TaskResult(name, 0, 0.0, "skipped", "dependency failed: " + ", ".join(failed_dependencies))
                    )
                    launched = True
                    continue
                if not set(task.dependencies) <= complete or set(task.resources) & held_resources:
                    continue
                pending.remove(name)
                held_resources.update(task.resources)
                running[executor.submit(_execute, task, context, registry)] = task
                launched = True
                if len(running) >= worker_count:
                    break
            if not running:
                if pending:
                    raise ExecutionError("Workflow cannot make progress")
                break
            if launched and len(running) < worker_count:
                continue
            finished, _ = wait(running, return_when="FIRST_COMPLETED")
            for future in finished:
                task = running.pop(future)
                held_resources.difference_update(task.resources)
                result = future.result()
                results.append(result)
                if result.status == "passed":
                    complete.add(task.name)
                else:
                    failed.add(task.name)
    except BaseException:
        registry.stop_all()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    failed_results = [result for result in results if result.status != "passed"]
    if failed_results:
        detail = "; ".join(f"{result.name}: {result.detail}" for result in failed_results)
        raise ExecutionError(f"Workflow failed: {detail}")
    return tuple(results)
