# Drift

Drift is a typed Python project platform with a fast, deterministic Ninja build backend. It replaces sprawling project scripts with one immutable declaration that can describe native targets, workflows, tests, benchmarks, artifacts, releases, GitHub publication, and explicit remote operations.

Drift 0.1 is experimental. It supports Python 3.12, Windows with MSVC, and Linux and macOS with GCC or Clang.
Pinned Git, archive, and vcpkg packages can be imported through Drift, Conan, CMake, Meson, MSBuild, Autotools,
Make, B2, SCons, prebuilt layouts, or header-only layouts. Installed system interfaces can be imported through pkg-config.

## Quick start

Create `drift.toml`:

```toml
[project]
api-version = 1
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

Ninja 1.13.1, CMake 3.31.6, Meson 1.12.0, Conan 2.31.2, and vcpkg 2026-07-27 are managed on demand under `DRIFT_HOME/tools`; only selected adapters are fetched. `DRIFT_NINJA`, `DRIFT_CMAKE`, `DRIFT_MESON`, `DRIFT_CONAN`, and `DRIFT_VCPKG` override them. Autotools, Make, B2, SCons, and pkg-config use their corresponding host tools.

## Commands

- `drift configure` validates the declaration and writes `build.ninja` and `compile_commands.json`.
- `drift build [targets...]` incrementally builds targets and reports phase timings; `-j`, `--dry-run`, `--explain`,
  and `--keep-going` expose useful execution controls without leaking backend syntax.
- `drift lock` resolves pinned package sources and replaces `drift.lock`; `--check` and `--diff` support CI review.
- `drift lock --sign KEY` signs the lock with SSH; `--verify-signature SIGNERS --signer ID` verifies it.
- `drift update` re-verifies exact sources and rewrites the lock, while `drift outdated` reports declaration drift.
- `drift fetch` downloads and verifies every package in `drift.lock`; `--offline` forbids network access.
- `drift inspect [packages...]` prints resolved adapters, cache keys, commands, outputs, and provenance as JSON.
- `drift doctor` verifies Python, the active compiler toolchain, Git, locked package sources, and cache location.
- `drift cache status` reports shared cache usage; `cache path` locates it and `cache clean CATEGORY --yes` removes
  an explicit `sources`, `binaries`, `tools`, `conan`, `vcpkg`, or `all` category.
- `drift cache export/import` moves cache archives between machines; `cache pull/push` uses HTTPS or file URLs.
- `drift run [project] [target] [-- arguments...]` builds and launches an executable target.
- `drift clean [targets...]` removes default or selected target outputs through Ninja.
- `drift install --prefix PATH` builds a conventional `include`, `lib`, and `bin` SDK with a verified manifest.
- `drift generate visual-studio`, `vscode`, and `xcode` create IDE frontends that delegate builds to Drift.
- `drift standalone --output drift.pyz` creates a standard-library-only portable zipapp. Tagged releases also
  publish native application archives that do not require Python.
- `drift graph` prints the validated target graph.
- `drift targets` lists user targets and marks defaults; `--all` includes imported package targets.
- `drift task [names...]` executes dependency-aware workflows with retries and resource locks.
- `drift test`, `drift benchmark`, and `drift artifact` run their typed declarations.
- `drift perf` records warm configure and no-op build latency in `.drift/performance.json`.
- `drift perf --budget FILE` turns platform-specific configure and no-op medians into a CI regression gate.
- `drift audit` writes a deterministic CycloneDX SBOM and bundled third-party license evidence.
- `drift release NAME` validates a release; `--publish` delegates publication to authenticated `gh`.
- `drift self-update` verifies a native release checksum and atomically updates the user installation.
- `drift remote NAME -- COMMAND...` uses the system SSH client for explicit remote work.
- `drift command PATH...` invokes sync or async provider-defined commands with typed options.

Builds that execute work report wall-clock total, configure, and Ninja time. Compile, archive, link, and action values
are accumulated job time, so they can exceed wall-clock time when Ninja runs work in parallel. No-op builds report
their total validation time.

Use `--target TRIPLE`, `--sysroot PATH`, and `--toolchain FILE` for cross builds. A Drift toolchain file is JSON with
`family`, `cc`, `cxx`, `linker`, and `archiver`; CMake `.cmake` and Meson cross files are also forwarded to those adapters.
Named `--profile` values cover Android, iOS, Emscripten, MinGW, and clang-cl. Native builds also support `--sanitize`,
`--coverage`, `--lto`, `--warnings`, `--unity`, and per-target `precompiled_header=`. `--hermetic` strips ambient
compiler and pkg-config flags and fixes locale, timezone, and source epoch for more reproducible builds.

Tagged releases include provenance attestations, checksums, SBOM and license reports, native archives, and
`scripts/install.sh` / `scripts/install.ps1`. A native installation can update itself without Python or uv.

See [the architecture](docs/architecture.md), [API reference](docs/api.md), [package guide](docs/packages.md), [Visual Studio guide](docs/visual-studio.md), [migration guide](docs/migration.md), [native fixture](examples/native), [SDL3 example](examples/sdl3-window), and [Meson package example](examples/inih) for the contracts behind those commands.

## Development

```console
uv sync
uv run drift task check
```

Drift is MIT licensed. It is standalone and has no runtime dependency on Castalia.
