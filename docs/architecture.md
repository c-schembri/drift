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

Project-generated state lives in `.drift`. Verified package sources live under `DRIFT_HOME/store`, and managed tools live
under `DRIFT_HOME/tools`; shared package builds live under `DRIFT_HOME/binaries`. All use the platform cache directory
when `DRIFT_HOME` is unset. Configuration directories are keyed by platform, target, architecture, compiler, build type,
sysroot, toolchain, and provider values. File discovery is sorted, root-confined,
excludes symlinks, and rejects case collisions. Generated files are replaced only when their bytes change, preserving
no-op performance.

Shared cache deletion is never implicit. `drift cache status` measures each ownership category, while cleanup requires
both an explicit category and `--yes`; every deletion target is checked as a strict child of `DRIFT_HOME`.

The runtime uses only the Python standard library. Build-system adapters bootstrap pinned tools on demand. Ninja and
CMake and vcpkg binaries and the Meson wheel have fixed content digests; Conan runs from an isolated environment containing an
exact package set. Cross builds select a target triple plus a sysroot or explicit JSON/upstream toolchain file.
Autotools and pkg-config intentionally inspect host tools and are therefore host integrations rather than hermetic package formats.

## Boundaries

The build graph owns source/header selection, compile and link interfaces, object/static/shared/executable targets, custom actions, external libraries, aliases, runtime bundles, and locked package target references. The workflow graph separately owns operational tasks. Tests, benchmarks, artifacts, releases, GitHub, and remotes are typed services referencing those declarations.

Drift's native source package layer intentionally has no registry or version solver. A package must be pinned to an exact
Git commit or archive digest and expose a native Drift project, a recognized upstream build description, or a trusted
local overlay. Adapter selection depends on manifests and host configuration, never on a package name. Conan recipes
may resolve their own transitive package graph and binary variants inside Conan's isolated cache.

The MSBuild adapter translates a checked-in Visual C++ project-reference closure directly into Drift. CMake and Meson
are configured out of tree and queried through their supported introspection formats. Autotools projects with a
generated `configure` script are installed into a private prefix behind one explicit action. Conan recipes are created
once, then their packaged C/C++ interface is deployed into project state. pkg-config is an explicit provider API for
host-installed interfaces. Visual Studio generation emits Makefile-style projects backed by Drift and Ninja rather than
a second MSBuild backend. Remote execution never occurs implicitly during a local build.
