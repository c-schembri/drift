# Drift

Drift is a typed Python project platform with a fast, deterministic Ninja build backend. It replaces sprawling project scripts with one immutable declaration that can describe native targets, workflows, tests, benchmarks, artifacts, releases, GitHub publication, and explicit remote operations.

Drift 0.1 is experimental. It supports Python 3.12, Windows with MSVC, and Linux with GCC or Clang. It intentionally does not resolve third-party packages: providers model prebuilt dependencies and retain control over how those files arrive.

## Quick start

Create `drift.toml`:

```toml
[project]
api-version = 0
provider = "build:project"
```

Then declare a graph in `build.py`:

```python
from driftbuild.api import ProjectApi, TestSpec


def project(api: ProjectApi):
    math = api.static_library(
        "math",
        sources=api.files("src/add.c"),
        public_headers=api.files("include/add.h"),
        include_dirs=("include",),
    )
    app = api.executable(
        "hello",
        sources=api.files("src/main.cpp"),
        dependencies=(api.private(math),),
    )
    api.test(TestSpec("hello", (".drift/build/win32-x86_64-auto-debug/bin/hello.exe",), build_targets=(app,)))
    return api.project("hello", defaults=(app,))
```

Run this checkout directly with Python 3.12; Drift has no third-party runtime dependencies:

```console
py -3.12 drift.py build path/to/project
py -3.12 drift.py lock path/to/project
py -3.12 drift.py run path/to/project
py -3.12 drift.py test path/to/project
```

After installing Drift as a command, use `drift build` inside a project or `drift build path/to/project` from elsewhere. `--root` remains available for scripts that prefer an explicit option.

Ninja 1.13.1 and CMake 3.31.6 are downloaded on demand from their official releases, checked against pinned SHA-256 digests, and stored under `.drift/tools`. CMake is fetched only when a dependency requires its adapter. Set `DRIFT_NINJA` or `DRIFT_CMAKE` to use explicitly managed executables instead.

## Commands

- `drift configure` validates the declaration and writes `build.ninja` and `compile_commands.json`.
- `drift build [targets...]` incrementally builds default or selected targets and reports build phase timings.
- `drift lock` resolves pinned package sources and replaces `drift.lock`.
- `drift fetch` downloads and verifies every package in `drift.lock`; `--offline` forbids network access.
- `drift run [project] [target] [-- arguments...]` builds and launches an executable target.
- `drift clean [targets...]` removes default or selected target outputs through Ninja.
- `drift generate visual-studio` writes a solution and Makefile-style projects under `.drift/visual-studio`.
- `drift graph` prints the validated target graph.
- `drift task [names...]` executes dependency-aware workflows with retries and resource locks.
- `drift test`, `drift benchmark`, and `drift artifact` run their typed declarations.
- `drift release NAME` validates a release; `--publish` delegates publication to authenticated `gh`.
- `drift remote NAME -- COMMAND...` uses the system SSH client for explicit remote work.
- `drift command PATH...` invokes sync or async provider-defined commands with typed options.

Builds that execute work report wall-clock total, configure, and Ninja time. Compile, archive, link, and action values
are accumulated job time, so they can exceed wall-clock time when Ninja runs work in parallel. No-op builds report
their total validation time.

See [the architecture](docs/architecture.md), [API reference](docs/api.md), [package guide](docs/packages.md), [Visual Studio guide](docs/visual-studio.md), [migration guide](docs/migration.md), [native fixture](examples/native), and [SDL3 example](examples/sdl3-window) for the contracts behind those commands.

## Development

```console
uv sync
uv run drift task check
```

Drift is MIT licensed. It is standalone and has no runtime dependency on Castalia.
