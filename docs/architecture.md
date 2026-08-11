# Architecture

Drift separates project policy from execution in four stages:

1. `drift.toml` selects an API version and a typed Python provider.
2. `ProjectApi` records immutable dataclasses. Provider evaluation must not compile, download, or mutate the source tree.
3. Graph validation checks identities, references, cycles, outputs, and workflow prerequisites before execution.
4. The Ninja backend lowers native targets into a stable out-of-tree build and emits `compile_commands.json`.
5. IDE frontends may project that validated graph for editing and debugging, but delegate builds back to Drift.

## State and reproducibility

All generated state lives in `.drift`. Configuration directories are keyed by platform, architecture, compiler selection, and build type. File discovery is sorted, root-confined, excludes symlinks, and rejects case collisions. Generated files are replaced only when their bytes change, preserving no-op performance.

The runtime uses only the Python standard library. Ninja is the only bootstrapped executable; its version and archive digests are fixed in `bootstrap.py`. Compiler discovery is host-only in v0.

## Boundaries

The build graph owns source/header selection, compile and link interfaces, object/static/shared/executable targets, custom actions, external libraries, aliases, and runtime bundles. The workflow graph separately owns operational tasks. Tests, benchmarks, artifacts, releases, GitHub, and remotes are typed services referencing those declarations.

Drift does not manage packages, translate its graph into an IDE's native build engine, sandbox commands, or perform cross-compilation in v0. Visual Studio generation emits Makefile-style projects backed by Drift and Ninja rather than a second MSBuild backend. Remote execution never occurs implicitly during a local build.
