# Architecture

Drift separates project policy from execution in seven stages:

1. `drift.toml` selects an API version and a typed Python provider.
2. `ProjectApi` records immutable dataclasses. Provider evaluation must not compile, download, or mutate the source tree.
3. `drift.lock` fixes external source identities and verified content digests without executing package providers.
4. Locked package projects are loaded from the content-addressed store and composed under collision-proof target names.
5. Graph validation checks identities, references, cycles, outputs, and workflow prerequisites before execution.
6. The Ninja backend lowers native targets into a stable out-of-tree build and emits `compile_commands.json`.
7. IDE frontends may project that validated graph for editing and debugging, but delegate builds back to Drift.

## State and reproducibility

Project-generated state lives in `.drift`. Verified package sources live in a shared content-addressed store under `DRIFT_HOME/store`, the platform cache directory when `DRIFT_HOME` is unset. Configuration directories are keyed by platform, architecture, compiler selection, and build type. File discovery is sorted, root-confined, excludes symlinks, and rejects case collisions. Generated files are replaced only when their bytes change, preserving no-op performance.

The runtime uses only the Python standard library. Ninja is the only bootstrapped executable; its version and archive digests are fixed in `bootstrap.py`. Compiler discovery is host-only in v0.

## Boundaries

The build graph owns source/header selection, compile and link interfaces, object/static/shared/executable targets, custom actions, external libraries, aliases, runtime bundles, and locked package target references. The workflow graph separately owns operational tasks. Tests, benchmarks, artifacts, releases, GitHub, and remotes are typed services referencing those declarations.

Drift v0 package support intentionally has no registry, version solver, transitive package graph, binary package format, or package action targets. A package must be pinned to an exact Git commit or archive digest and expose a native Drift project, a supported upstream build description, or a trusted local overlay. The MSBuild importer reads the native C/C++ subset of a `.vcxproj` into Drift's graph; it does not invoke MSBuild. Drift does not otherwise translate its graph into an IDE's native build engine, sandbox commands, or perform cross-compilation in v0. Visual Studio generation emits Makefile-style projects backed by Drift and Ninja rather than a second MSBuild backend. Remote execution never occurs implicitly during a local build.
