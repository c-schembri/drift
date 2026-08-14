# Provider API v1

Import the supported surface from `driftbuild.api`. Construct declarations through the supplied `ProjectApi`; do not instantiate internal backend classes.

## Files and targets

`api.files(*paths)` validates explicit repository files. `api.tree(root, include=..., exclude=...)` performs deterministic discovery. Target constructors accept file sets and expose compile/link behavior:

```python
core = api.object_library("core", sources=api.tree("src/core", include=("**/*.cpp",)))
support = api.static_library(
    "support",
    sources=api.files("src/support.cpp"),
    public_headers=api.tree("include", include=("**/*.h",)),
    include_dirs=("include",),
    defines=("SUPPORT_ENABLED=1",),
    objects=(core,),
)
app = api.executable("app", sources=api.files("src/main.cpp"), dependencies=(api.private(support),))
```

Targets accept `precompiled_header="include/pch.h"`. Windows `.rc` sources are compiled and linked like other native
sources when using an MSVC-compatible toolchain. Global sanitizer, coverage, LTO, warnings, and unity modes are
configuration selections rather than provider branches, so the same declaration remains portable.

Use `api.native_profile(...)` to share native settings across targets without creating an artificial library:

```python
windows = api.native_profile("windows", defines=("UNICODE",), link_arguments=("user32.lib",))
app = api.executable("app", sources=api.files("main.cpp", "app.rc"), profiles=(windows,))
```

Use `api.public(target)` when a dependency's compile interface should propagate and `api.private(target)` when it should not. `api.dependency(...)` models a prebuilt or interface-only dependency, including include directories, definitions, libraries, link arguments, and runtime files. Drift deliberately does not fetch it. `api.pkg_config(name, static=False)` resolves the equivalent interface from a host-installed `.pc` package.

`api.package(...)` declares a locked source package separately from its eventual compile/link interface. Sources are exact
`api.archive(url, sha256, strip_prefix=...)`, `api.git(url, revision, submodules=..., track=...)`, or
`api.vcpkg(port, baseline, features=...)` values. `api.package` accepts portable `options`, `features`, `patches`,
`components`, and `linkage="auto" | "static" | "shared"`, plus an
optional adapter override. Drift detects upstream Drift, Visual C++, CMake, Meson, Autotools, Conan, Make, B2, SCons,
prebuilt, and header-only projects from materialized content. Pass the returned package directly to
`api.public(...)` or `api.private(...)` to use its imported default library. Use `package.target("name")` only to select
another exported target explicitly; `package.component("name")` is an equivalent component-oriented spelling. See the
[package guide](packages.md) for locking and current limitations.

`api.msbuild(project_file, ...)` is an explicit override for ambiguous Visual C++ repositories. Normal packages do not
need it. Drift reads the selected project and its `ProjectReference` closure but never invokes MSBuild.

`api.command_action(...)` defines an external command with explicit inputs, outputs, environment, depfile policy,
timeout, and Ninja pool. `api.provider_action("tools.codegen:generate", ...)` instead imports a synchronous project
handler through Drift's own runtime. It works in native installations without system Python and automatically tracks
the handler module as an implicit input. Register constrained pools with `api.pool(PoolSpec(...))`. Arguments may use
`{root}`, `{build}`, `{out}`, `{out:N}`, and `{in:N}`; Drift expands them without invoking a shell. Wrap either action
with `custom_target` or `external_library`.

`runtime_bundle(..., clean=True)` copies explicit files beside a stamp target and removes stale files previously owned
by the same bundle. `api.deploy(source, "plugins/name.dll")` maps one destination. `api.deploy_tree(files, "assets",
"Data")` preserves paths below a source root. Deployments work on target and prebuilt-dependency runtime files. `alias`
groups targets.

`api.cargo(...)` adds Cargo-owned binaries or workspaces to the same target graph. Drift schedules Cargo through Ninja,
maps its release build type, discovers every local workspace package through `cargo metadata`, tracks its Rust,
manifest, lock, build-script, and Cargo configuration inputs through a depfile, and leaves compilation to Cargo's incremental
cache. Set `run_target=` to make a Cargo binary available through `drift run`. Use
`api.cargo_static_library(...)` when a native target links an explicit Cargo-produced archive:

```python
server = api.cargo(
    "server",
    manifest="Server/Cargo.toml",
    packages=("server",),
    targets=("server",),
    run_target="server",
    target_directory="Server/target",
)
ffi = api.cargo_static_library(
    "ffi",
    manifest="rust/ffi/Cargo.toml",
    artifact_name="sample_ffi",
    include_dirs=("rust/ffi/include",),
)
api.cargo_workspace("rust", manifest="Cargo.toml")
```

Cargo declarations also accept workspace, feature, environment, extra-argument, explicit-input, and explicit-output
selections. When binary targets or a static-library artifact are selected, Drift discovers Cargo's emitted artifact and
copies it to a stable configuration output automatically. An explicit `target_directory` is repository-confined;
omitting it uses the active Drift build directory. `cargo_workspace` registers format, check, Clippy, and test checks;
the set can be narrowed with `checks=`.

## Platform services

- `TaskSpec` declares workflow dependencies, subprocess or sync/async handler, retries, timeout, and resource locks.
  A task may directly reference one `test=`, `matrix=`, or `targets=` operation, with optional `configuration=`
  overrides, so suites do not need to launch nested Drift processes. `provider_command=` invokes a registered provider
  command in-process.
- `TestSpec(target=app)` builds and runs a target's declared `run_command`, or its first output by default. Add
  `arguments=`, or use `handler="tools.tests:probe"` for a project-owned test driver. `isolated=True` supplies a
  temporary home, app-data, config, and temp directory; `{temp}` in environment values resolves there.
- `SuiteSpec` composes `TaskSpec` nodes behind `drift test`. Dependencies preserve ordering, resources serialize only
  conflicting work, independent tasks continue after a failure, and `exclusive=True` locks the complete suite.
  Command process trees are stopped on timeout or interruption.
- `MatrixSpec` declares Cartesian build or test axes such as build type, compiler, profile, and provider values.
- `BenchmarkSpec` declares warmups, repetitions, and build prerequisites.
- `ArtifactSpec` creates deterministic ZIP or tar.gz archives from source files and target outputs.
- `ReleaseSpec` ties versions and artifacts to a tag. `GitHubSpec` enables explicit `gh` publication.
- `RemoteSpec` configures explicit SSH execution and copy operations.
- `CommandSpec` and `OptionSpec` expose typed provider commands. `build_targets=` builds prerequisites first and makes
  configured paths available through `CommandContext.outputs`. `CommandGroupSpec` documents intermediate command-tree
  nodes used by `drift command ... --help` and shell completion. Handlers receive `CommandContext` and may be synchronous
  or asynchronous. Set `passthrough=True` for a command family that owns its own parser and must receive the remaining
  arguments unchanged.
- Commands rooted at `("run", ...)` extend `drift run` with project-specific launch workflows. They can build declared
  targets first and then supply configuration, materialize runtime state, or select one of several related programs.
  For a direct launch alias, set `run_target=` instead of a handler; Drift records that route in configured state so
  unchanged `drift run` invocations can build through cached Ninja state without reevaluating the provider graph.

## Project options and local SDKs

Declare project-specific configuration before using it:

```python
flavor = api.option("flavor", choices=("developer", "retail"), default="developer")
workers = api.option("workers", value_type=int, default=4)
```

Users select values with `-D flavor=retail`. Unknown, duplicate, malformed, and unsupported values fail during project
loading. Declared options also form valid matrix axes.

`api.find_program(...)` and `api.find_file(...)` resolve required host tools and files from declared roots or environment
variables; programs also search `PATH`. Drift records the selected path and relevant environment in configuration state.

`api.local_sdk(...)` imports a machine-local SDK from an environment variable or fallback root without embedding its
layout in the project provider:

```python
vulkan = api.local_sdk(
    "vulkan",
    descriptor="sdk/vulkan.json",
    environment=("VULKAN_SDK",),
)
```

Descriptors can declare include and library directories, libraries, defines, compile/link arguments, runtime files,
optional files, runtime globs, and conditional variants selected by platform, architecture, compiler, build type, or a
project option. `${root}`, `${project}`, `${platform}`, `${architecture}`, `${build_type}`, and `${option:name}` are
available in descriptor values. Descriptor timestamps and SDK-root environment variables invalidate Drift's warm
configuration cache.

Set `materialize_to=` and add a descriptor `materialize` recipe to make a minimal, project-owned SDK snapshot explicit:

```python
sdl = api.local_sdk(
    "sdl3",
    descriptor="sdk/sdl3.json",
    environment=("SDL3_SDK",),
    roots=("third_party/SDL",),
    materialize_to="third_party/SDL",
)
```

```json
{"materialize": {"required": ["include", "lib"], "optional": ["LICENSE*"]}}
```

`drift sdk materialize` replaces each declared destination from those root-confined selections. Pass SDK names to
materialize only a subset. The normal configure/build path never mutates SDK sources.

Provider command implementations may import stable process, locking, copy, removal, environment, and timestamp helpers
from `driftbuild.api`, including `run`, `OwnedProcess`, `FileLock`, `copy_file`, and `outputs_current`.

`api.python_requirements("requirements.txt")` declares hashed Python dependencies needed by provider commands. Drift
materializes them with its bundled pip into a content-addressed shared environment, activates them before command
handlers run, and rejects a missing environment under `--offline`. Native Drift installations also re-enter their
bundled Python runtime for project subprocesses using `sys.executable`, so project tooling does not need `uv` or a
separate virtual environment.

## Project requirement

Set `requires-drift = "==0.4.2"` in the manifest's `[project]` table to pin a project to one Drift release. Every
provider-loading command validates the constraint. `drift bootstrap` checks it without loading the provider, and
`drift bootstrap --install` installs an exact pinned release through Drift's verified self-updater.

API v1 is the stable provider contract. Drift continues to load API v0 manifests for migration, but new declarations
should use `api-version = 1`. Additive fields and new declaration types may be introduced within v1; existing names,
defaults, and semantics will not be changed incompatibly. A future incompatible API will require an explicit manifest
version and will not silently reinterpret an older provider.
