"""Deterministic dependency-aware workflow execution."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from driftbuild.errors import ExecutionError
from driftbuild.model import CommandContext, ProjectSpec, TaskSpec
from driftbuild.process import run


@dataclass(frozen=True)
class TaskResult:
    """One workflow task outcome."""

    name: str
    attempts: int
    duration_seconds: float


async def _await(value: Awaitable[Any]) -> Any:
    return await value


def _invoke(handler: Callable[..., Any], context: CommandContext) -> None:
    value = handler(context)
    if inspect.isawaitable(value):
        asyncio.run(_await(value))


def _execute(task: TaskSpec, context: CommandContext) -> TaskResult:
    started = time.perf_counter()
    last_error: Exception | None = None
    for attempt in range(1, task.retries + 2):
        try:
            if task.command is not None:
                environment = dict(context.environment)
                environment.update(task.environment)
                run(
                    task.command,
                    cwd=context.project_root,
                    environment=environment,
                    timeout_seconds=task.timeout_seconds,
                )
            elif task.handler is not None:
                _invoke(task.handler, context)
            return TaskResult(task.name, attempt, time.perf_counter() - started)
        except Exception as error:
            last_error = error
    assert last_error is not None
    raise ExecutionError(f"Task {task.name} failed after {task.retries + 1} attempt(s): {last_error}") from last_error


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
    running: dict[Future[TaskResult], TaskSpec] = {}
    held_resources: set[str] = set()
    results: list[TaskResult] = []
    worker_count = jobs or max(1, min(32, os.cpu_count() or 1))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="drift-task") as executor:
        while pending or running:
            launched = False
            for name in sorted(pending):
                task = tasks[name]
                if not set(task.dependencies) <= complete or set(task.resources) & held_resources:
                    continue
                pending.remove(name)
                held_resources.update(task.resources)
                running[executor.submit(_execute, task, context)] = task
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
                complete.add(task.name)
    return tuple(results)
