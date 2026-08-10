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

Install and use it:

```console
uv tool install drift-build
drift build
drift test
```

Ninja 1.13.1 is downloaded from its official GitHub release, checked against a pinned SHA-256 digest, and stored under `.drift/tools`. Set `DRIFT_NINJA` to use an explicitly managed executable instead.

## Commands

- `drift configure` validates the declaration and writes `build.ninja` and `compile_commands.json`.
- `drift build [targets...]` incrementally builds default or selected targets.
- `drift graph` prints the validated target graph.
- `drift task [names...]` executes dependency-aware workflows with retries and resource locks.
- `drift test`, `drift benchmark`, and `drift artifact` run their typed declarations.
- `drift release NAME` validates a release; `--publish` delegates publication to authenticated `gh`.
- `drift remote NAME -- COMMAND...` uses the system SSH client for explicit remote work.
- `drift command PATH...` invokes sync or async provider-defined commands with typed options.

See [the architecture](docs/architecture.md), [API reference](docs/api.md), [migration guide](docs/migration.md), and [native fixture](examples/native) for the contracts behind those commands.

## Development

```console
uv sync
uv run drift task check
```

Drift is MIT licensed. It is standalone and has no runtime dependency on Castalia.
