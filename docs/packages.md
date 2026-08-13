# Locked Packages

Drift packages are immutable external source projects. Package declarations do not download files or execute package
providers. `drift lock` materializes each exact source, records its verified content digest in `drift.lock`, and makes
subsequent builds reproducible.

## Declaring a package

A package can contain its own `drift.toml` and provider:

```python
def project(api):
    zlib = api.package(
        "zlib",
        source=api.git(
            "https://github.com/madler/zlib.git",
            revision="0123456789abcdef0123456789abcdef01234567",
        ),
    )
    app = api.executable("app", sources=api.files("src/main.c"), dependencies=(api.private(zlib),))
    return api.project("app", defaults=(app,))
```

Archives require a SHA-256 digest and may remove one explicit top-level directory:

```python
fmt = api.package(
    "fmt",
    source=api.archive(
        "https://example.invalid/fmt-11.0.2.tar.gz",
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        strip_prefix="fmt-11.0.2",
    ),
    overlay="dependencies/fmt.py",
)
```

An upstream project with a recognized build description needs no handwritten build recipe:

```python
sdl = api.package(
    "sdl3",
    source=api.git("https://github.com/libsdl-org/SDL.git", revision="0123456789abcdef0123456789abcdef01234567"),
)
```

Options, optional features, patches, and submodules are part of the lock and binary-cache identity:

```python
library = api.package(
    "library",
    source=api.git("https://example.invalid/library.git", REVISION, submodules=True),
    options={"shared": False, "tests": False},
    features=("simd",),
    patches=("patches/library.patch",),
    adapter="meson",  # only needed to override normal manifest detection
)
```

vcpkg ports use an exact registry baseline, so the declaration remains minimal and reproducible:

```python
zlib = api.package("zlib", source=api.vcpkg("zlib", VCPKG_BASELINE))
```

Drift chooses adapters from repository manifests and the selected host configuration, not from the package name:

- A `conanfile.py` is created and its packaged C/C++ interface is imported.
- On Windows, a matching Visual C++ project and its reference closure can be translated directly.
- CMake is configured once and queried through its File API codemodel.
- Meson is configured once and queried through `meson-info`; its generated Ninja graph performs the package build.
- A generated Autotools `configure` script is configured out of tree and built into a private install prefix.
- `Jamroot`, `SConstruct`, and `Makefile` select isolated B2, SCons, and Make install adapters.
- Conventional `include`, `lib`, and `bin` layouts support prebuilt and header-only packages.

Imported builds remain behind explicit Ninja action edges, so no-op Drift builds do not rerun them. `conanfile.txt` is
a consumer manifest rather than a package recipe and is not treated as a build description.

Drift manages pinned Ninja, CMake, Meson, and Conan tools on demand under `DRIFT_HOME/tools`, shared by all projects.
Consumers do not install them separately.
Autotools currently requires host `sh` and `make` and is supported on POSIX hosts.

Configured package builds are shared across projects under `DRIFT_HOME/binaries`, keyed by verified source, adapter,
options, features, target, sysroot, and toolchain. Run `drift inspect` to see the resolved adapter, provenance, cache path,
commands, and output hashes for materialized artifacts.
`drift doctor` additionally verifies the current lock against already cached package content without contacting the
network, which makes it suitable for CI image and offline-environment diagnostics.

Host-installed libraries can be declared explicitly through pkg-config:

```python
sdl = api.pkg_config("sdl3")
app = api.executable("app", sources=api.files("main.c"), dependencies=(sdl,))
```

This interface reflects the host and is intentionally not recorded as a source package in `drift.lock`.

When a repository exports several unrelated libraries and no default can be inferred, select one explicitly with
`package.target("upstream-target")`. `build=api.msbuild(...)` remains an escape hatch for ambiguous Visual C++ trees;
it is not expected in ordinary package declarations.

The overlay is trusted code owned by the root project. It receives a `ProjectApi` rooted at the verified package source,
so an upstream project does not need to know about Drift:

```python
def project(api):
    library = api.static_library(
        "fmt",
        sources=api.tree("src", include=("**/*.cc",)),
        public_headers=api.tree("include", include=("**/*.h",)),
        include_dirs=("include",),
    )
    return api.project("fmt", defaults=(library,))
```

## Workflow

```console
drift lock
drift fetch
drift --offline build
```

Commit `drift.lock`. A normal build may download content already identified by that lock, but it never changes the lock
or resolves another revision. `--offline` rejects missing network content. `drift fetch` also rehashes cached source trees
and reports local corruption.

Set `DRIFT_HOME` to choose the Drift state directory. Package sources are shared by content digest under
`DRIFT_HOME/store/sources`; otherwise Drift uses the platform cache directory. The project `.drift` directory continues
to hold generated Ninja files and configuration-specific outputs.

## Security and limits

- Archive digests are mandatory; Git revisions must be complete commit hashes.
- HTTP archives, archive path traversal, symbolic links, special files, oversized archives, and checksum mismatches are
  rejected.
- Package credentials are supplied by the system URL or Git credential mechanisms and are never written to `drift.lock`.
- Package providers and build-system adapters run only after locked content is materialized. CMake configuration writes
  only beneath shared content-addressed build directories. Local overlays remain available when an upstream
  cannot be described by a supported adapter.
- Drift source packages remain a flat, explicitly pinned set. Conan recipes may independently resolve transitive
  dependencies and binary variants; Drift does not duplicate Conan's solver or registry.
