# Provider API v0

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

Use `api.public(target)` when a dependency's compile interface should propagate and `api.private(target)` when it should not. `api.dependency(...)` models a prebuilt or interface-only dependency, including include directories, definitions, libraries, link arguments, and runtime files. Drift deliberately does not fetch it.

`api.package(...)` declares a locked source package separately from its eventual compile/link interface. Sources are exact
`api.archive(url, sha256, strip_prefix=...)` or `api.git(url, revision)` values. Select an exported target with
`package.target("name")`, then pass it to `api.public(...)` or `api.private(...)`. See the [package guide](packages.md)
for locking, local overlays, offline operation, and current limitations.

Pass a package handle directly to `api.public(...)` or `api.private(...)` to consume its single default target. Drift
first looks for an upstream Drift provider, then detects supported conventional native projects. Explicit target and
build descriptions are escape hatches for ambiguous packages, not routine dependency boilerplate.

`api.msbuild(project_file, ...)` imports the native C/C++ subset of an upstream `.vcxproj` directly into Drift. Pass it
as `package(..., build=...)`; Drift reads the selected source list and compile/link interface but never invokes MSBuild.

`api.command_action(...)` defines a custom action with explicit inputs, outputs, environment, depfile policy, timeout, and Ninja pool. Register constrained pools with `api.pool(PoolSpec(...))`. Command arguments may use the exact tokens `{root}`, `{build}`, `{out}`, `{out:N}`, and `{in:N}`; Drift expands them without invoking a shell. Wrap the action with `custom_target` or `external_library`. `runtime_bundle` copies explicit files beside a stamp target. `alias` groups targets.

## Platform services

- `TaskSpec` declares workflow dependencies, subprocess or sync/async handler, retries, timeout, and resource locks.
- `TestSpec` declares labels, build prerequisites, environment, and timeout.
- `BenchmarkSpec` declares warmups, repetitions, and build prerequisites.
- `ArtifactSpec` creates deterministic ZIP or tar.gz archives from source files and target outputs.
- `ReleaseSpec` ties versions and artifacts to a tag. `GitHubSpec` enables explicit `gh` publication.
- `RemoteSpec` configures explicit SSH execution and copy operations.
- `CommandSpec` and `OptionSpec` expose typed provider commands. Handlers receive `CommandContext` and may be synchronous or asynchronous.

The v0 API is experimental. Breaking changes remain possible until API version 1.
