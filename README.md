# Drift

[![CI](https://github.com/c-schembri/drift/actions/workflows/ci.yml/badge.svg)](https://github.com/c-schembri/drift/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/c-schembri/drift)](https://github.com/c-schembri/drift/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Drift is a typed build and project tool for native software. Describe the project once in Python, then use one command
to build, run, test, benchmark, package, publish, and generate IDE projects.

```console
drift build
drift run
drift test
```

Drift lowers first-party C and C++ targets to Ninja for fast incremental builds. External libraries can keep their
existing CMake, Meson, Visual C++, Autotools, Conan, vcpkg, Make, B2, SCons, pkg-config, prebuilt, or header-only
layout: Drift detects the appropriate adapter, imports its compile and link interface, and caches the result.

The native Drift release does not require Python, uv, Ninja, CMake, or Meson to be installed. Drift ships as a native
application and downloads each pinned build tool only when the active graph needs it.

> Drift 0.3 is experimental. Provider API v1 is stable within the 0.3 release line, but the wider command and package
> surface is still evolving.

## Why Drift?

- **One project graph.** Sources, headers, libraries, executables, tests, tasks, benchmarks, artifacts, and releases use
  the same typed declaration.
- **Fast local builds.** Ninja owns scheduling and incremental execution. Drift avoids reevaluating unchanged providers
  on the no-op path.
- **Small project files.** Declare the library you want rather than reproducing its upstream build machinery.
- **Reproducible dependencies.** Git commits, archive hashes, vcpkg baselines, tool versions, options, patches, and
  exported components are captured in `drift.lock`.
- **No second IDE build.** Visual Studio, VS Code, and Xcode projects delegate back to the same Drift graph.
- **A real project interface.** Cross builds, Cargo targets, tests, deployment, CI tasks, artifacts, SBOMs, releases,
  and remote operations are explicit parts of the project instead of unrelated shell scripts.

## Install

Download the latest checksum-verified native release.

**Windows (PowerShell)**

```powershell
irm https://github.com/c-schembri/drift/releases/latest/download/install.ps1 | iex
$env:Path = "$env:LOCALAPPDATA\drift\bin;$env:Path"
drift --version
```

Add `%LOCALAPPDATA%\drift\bin` to your user `PATH` to make the command available in future terminals. If
`DRIFT_HOME` is set, use `%DRIFT_HOME%\bin` instead.

**Linux and macOS**

```sh
curl -fsSL https://github.com/c-schembri/drift/releases/latest/download/install.sh | sh
export PATH="${XDG_CACHE_HOME:-$HOME/.cache}/drift/bin:$PATH"
drift --version
```

Add that export to your shell profile. Set `DRIFT_VERSION=0.4.1` when running either installer to select an exact
release. Native archives are currently published for Windows x86_64, Linux x86_64, and macOS arm64.

Once installed, Drift updates itself without Python or uv:

```console
drift self-update
```

## Quick Start

A Drift project has a small manifest and a Python provider:

```text
hello/
|-- drift.toml
|-- build.py
`-- src/
    `-- main.cpp
```

`drift.toml` selects the provider API and can pin the Drift release used by the project:

```toml
[project]
api-version = 1
provider = "build:project"
requires-drift = "==0.4.1"
```

`build.py` declares the graph through the supported `driftbuild.api` surface:

```python
from driftbuild.api import ProjectApi


def project(api: ProjectApi):
    hello = api.executable(
        "hello",
        sources=api.files("src/main.cpp"),
    )
    return api.project("hello", defaults=(hello,))
```

Build and run it from the project directory:

```console
drift build
drift run
```

Drift discovers `drift.toml` from the current directory and its parents. A project directory can also be passed
directly, so `drift build examples/native` works from this repository root. Build output stays under `.drift`; scripts
can ask for the configured path instead of reconstructing it:

```console
drift output hello
```

## Libraries And Interfaces

Targets expose compile and link interfaces rather than raw command fragments. Public dependencies propagate their
interface to consumers; private dependencies stop at the target that names them.

```python
from driftbuild.api import ProjectApi


def project(api: ProjectApi):
    math = api.static_library(
        "math",
        sources=api.files("src/add.c"),
        public_headers=api.files("include/add.h"),
        include_dirs=("include",),
        defines=("MATH_FAST=1",),
    )
    calculator = api.executable(
        "calculator",
        sources=api.files("app/main.cpp"),
        dependencies=(api.private(math),),
    )
    return api.project("calculator", defaults=(calculator,))
```

Drift supports object, static, shared, and executable targets; generated actions; aliases; precompiled headers;
Windows resource sources; runtime files; and destination-preserving deployment. `api.native_profile()` shares native
settings across related targets without creating an artificial library. File inputs can be explicit with `api.files()`
or discovered deterministically with `api.tree()`.

See the [provider API reference](docs/api.md) for the complete target surface.

## Remote Dependencies

Declare what you want and pin the source. This SDL3 example does not contain an SDL-specific build recipe:

```python
from driftbuild.api import ProjectApi

SDL_REVISION = "8e37db5e797b6167f3a00d697d816a684bd259c7"


def project(api: ProjectApi):
    sdl3 = api.package(
        "sdl3",
        source=api.git("https://github.com/libsdl-org/SDL.git", SDL_REVISION),
    )
    window = api.executable(
        "window",
        sources=api.files("src/main.c"),
        dependencies=(api.private(sdl3),),
    )
    return api.project("window", defaults=(window,))
```

Resolve the source once, commit the lock, then build normally:

```console
drift lock
drift build
git add drift.lock
```

Drift chooses adapters from the package contents and selected host, never from a hardcoded package-name registry.

| Upstream input | Drift behavior |
| --- | --- |
| `drift.toml` | Composes the package's Drift graph, including transitive Drift packages |
| `.vcxproj` | Translates the Visual C++ project-reference closure directly on Windows |
| `CMakeLists.txt` | Configures with pinned CMake and imports the File API codemodel |
| `meson.build` | Configures with pinned Meson and imports `meson-info` |
| `configure` | Builds and installs into an isolated prefix with Autotools |
| `conanfile.py` | Uses an isolated pinned Conan environment and imports the package interface |
| vcpkg port | Resolves from an exact registry baseline and selected features |
| `Makefile`, `Jamroot`, `SConstruct` | Uses conventional staged Make, B2, or SCons installation |
| `include`, `lib`, `bin` | Imports conventional prebuilt or header-only layouts |
| pkg-config name | Imports an explicitly requested host-installed interface |

CMake and Meson are dependency adapters, not required project entry points. Drift runs their configure step when a
locked dependency requires it, records the normalized graph in the shared binary cache, and does not rerun it on every
no-op build. First-party Drift targets continue to compile directly through Ninja.

The normal locked workflow is:

```console
drift lock                 # resolve exact sources and replace drift.lock
drift fetch                # prefill and verify the source cache
drift --offline build      # reject all network access
drift inspect              # explain adapters, cache keys, commands, and outputs
drift audit                # write an SBOM and third-party license report
```

See [Locked Packages](docs/packages.md) for components, linkage selection, patches, signatures, binary caching,
transitive packages, overlays, and security limits.

## Everyday Commands

| Command | Purpose |
| --- | --- |
| `drift configure` | Validate the graph and generate Ninja files plus `compile_commands.json` |
| `drift build [targets...]` | Incrementally build defaults or named targets and print phase timings |
| `drift run [target] [-- args...]` | Build and launch an executable or a declared project run workflow |
| `drift test [names...]` | Build and run declared tests |
| `drift clean [targets...]` | Remove selected outputs through Ninja |
| `drift targets` | List declared targets and defaults |
| `drift output TARGET --json` | Return configured outputs for scripts and deployment tooling |
| `drift doctor` | Check the compiler, Git, lock, package cache, and managed tools |
| `drift graph` | Print the validated target graph as JSON |
| `drift sdk materialize [names...]` | Replace declared local SDK snapshots from descriptor selections |

Global configuration options come before the command:

```console
drift --build-type release build
drift --compiler clang --warnings error test
drift --sanitize address run
drift --hermetic --offline build
```

Use `--target TRIPLE`, `--sysroot PATH`, and `--toolchain FILE` for cross compilation. Named profiles cover Android,
iOS, Emscripten, MinGW, and clang-cl. LTO, coverage, unity builds, warning policy, and sanitizer selection remain command
configuration, so providers do not need platform branches for them.

## More Than Compilation

The provider can declare project operations alongside build targets:

| Declaration | Command | Use |
| --- | --- | --- |
| `TestSpec` | `drift test` | Target-bound tests, labels, timeouts, and isolated environments |
| `SuiteSpec` | `drift test` | Composite test DAGs with shared resources and exclusive-host locks |
| `TaskSpec` | `drift task` | Dependency-aware workflows with retries and resource locks |
| `MatrixSpec` | `drift matrix` | Build and test matrices across compilers, profiles, and provider values |
| `BenchmarkSpec` | `drift benchmark` | Warmed, repeated project benchmarks |
| `ArtifactSpec` | `drift artifact` | Deterministic ZIP and tar.gz artifacts |
| `ReleaseSpec` | `drift release` | Versioned releases and explicit GitHub publication |
| `CommandSpec` | `drift command` | Typed project-specific command trees and completion |
| `RemoteSpec` | `drift remote` | Explicit SSH execution and file transfer |

`drift perf` measures Drift's own warm-configure and no-op overhead and can enforce a checked-in platform budget in CI.
Builds that execute work report wall-clock, configure, Ninja, compile, archive, link, and action timings.

Project options are declared and typed in the provider, then selected with `-D name=value`. Target-bound tests and
commands build their prerequisites and consume configured outputs without reconstructing `.drift` paths. Project-owned
Python actions run through Drift's bundled runtime, including native installs that have no system Python. Composite
suites keep dependency ordering and resource locks in the graph rather than in a custom scheduler. Their tasks can
reference declared tests, target builds, matrices, and provider commands directly, avoiding nested Drift processes.
Projects with third-party Python command dependencies can declare a hashed requirements file with
`api.python_requirements(...)`; Drift materializes and activates a shared environment with its bundled runtime, without
requiring `uv`.

Runtime deployment supports explicit mappings and preserved directory trees. Clean bundles remove only files they
previously owned. Machine-local SDK layouts live in small JSON descriptors with platform and project-option variants;
descriptor edits and SDK-root environment changes invalidate the warm configuration cache. `api.find_program()` and
`api.find_file()` make host tool discovery explicit and cache-aware. An SDK descriptor can also own the exact file
selection used by the explicit `drift sdk materialize` command.

## Cargo

Cargo remains the compiler, package manager, and incremental engine for Rust. Drift can place Cargo-owned binaries,
workspaces, and static libraries into the wider project graph without guessing paths inside Cargo's target directory:

```python
server = api.cargo(
    "server",
    manifest="Server/Cargo.toml",
    packages=("server",),
    targets=("server",),
    run_target="server",
)
api.cargo_workspace("rust", manifest="Cargo.toml")
```

Drift reads Cargo metadata to discover every local workspace package and feeds its sources, manifests, lockfile,
build scripts, and Cargo configuration to Ninja through a depfile. It reads Cargo's JSON artifact stream, publishes
stable outputs, and runs the exact emitted binary through `drift run`. Workspace declarations expose format, check,
Clippy, and test checks to Drift workflows.

## IDEs

Generate editor metadata from the validated graph:

```console
drift generate visual-studio
drift generate vscode
drift generate xcode
```

Generated projects are frontends, not alternate build systems. Build, clean, run, and debug commands delegate back to
Drift, keeping command-line and IDE behavior aligned. See the [Visual Studio guide](docs/visual-studio.md) for details.

## Project State

- `.drift/` contains project-local generated graphs, object files, outputs, and performance records.
- `DRIFT_HOME/store` contains verified, content-addressed package sources.
- `DRIFT_HOME/binaries` contains package builds shared across projects.
- `DRIFT_HOME/tools` contains pinned tools fetched on demand.
- `DRIFT_HOME/python` contains content-addressed provider-command Python environments.

`DRIFT_HOME` defaults to `%LOCALAPPDATA%\drift` on Windows and `${XDG_CACHE_HOME:-$HOME/.cache}/drift` elsewhere.
Drift never deletes a shared cache implicitly; `drift cache clean CATEGORY --yes` requires an explicit ownership
category and confirmation.

## Version Pinning And Bootstrap

Projects can require an exact Drift release in `drift.toml`:

```toml
[project]
api-version = 1
provider = "build:project"
requires-drift = "==0.4.1"
```

```console
drift bootstrap             # verify the installed version
drift bootstrap --install   # install the exact required native release
```

Tagged releases contain checksums, native archives, a CycloneDX SBOM, third-party license evidence, and GitHub build
provenance attestations.

## Support

CI currently exercises Windows x86_64 with MSVC, Linux x86_64 with GCC and Clang, and macOS arm64 with Clang. Cross
profiles cover Android, iOS, Emscripten, MinGW, and clang-cl when their SDK or toolchain is available.

Ninja, CMake, Meson, Conan, and vcpkg are managed and pinned by Drift. Autotools, Make, B2, SCons, pkg-config, compilers,
platform SDKs, Git, SSH, and `gh` are host integrations and must be available when the project selects them.

## Documentation And Examples

- [Architecture](docs/architecture.md): evaluation, graph validation, execution, caching, and trust boundaries
- [Provider API](docs/api.md): targets, dependencies, Cargo, tests, tasks, matrices, artifacts, releases, and commands
- [Locked packages](docs/packages.md): sources, adapters, components, caching, signatures, and offline builds
- [Visual Studio](docs/visual-studio.md): solution generation, debugging, and delegated builds
- [Migration guide](docs/migration.md): moving imperative project scripts to Drift declarations
- [Native example](examples/native): libraries, executable, runtime assets, test, benchmark, and artifact
- [SDL3 example](examples/sdl3-window): one cross-platform declaration backed by upstream SDL build metadata
- [Compatibility fixtures](compatibility): real CMake, Meson, Autotools, Conan, vcpkg, and pkg-config consumers

## Development

Development uses Python 3.12 and uv; installed users do not need either:

```console
uv sync
uv run drift task check
```

The equivalent individual checks are `uv run ruff check .`, `uv run mypy driftbuild`, and `uv run pytest`.

Drift is licensed under the [MIT License](LICENSE). It is standalone and has no runtime dependency on Castalia.
