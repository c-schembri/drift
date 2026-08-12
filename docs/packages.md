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

Drift chooses adapters from repository manifests and the selected host configuration, not from the package name. On
Windows it can translate a matching Visual C++ project and its references directly into the Ninja graph. For portable
CMake projects it configures once out of tree, reads the CMake File API codemodel, and caches the normalized graph. CMake
builds remain behind explicit Ninja action edges, so no-op Drift builds do not rerun them.

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
  only beneath the consuming project's `.drift/imports` directory. Local overlays remain available when an upstream
  cannot be described by a supported adapter.
- The first package format supports a flat set of source packages. Transitive package declarations, registries, version
  constraints, and prebuilt package variants are not supported yet; Drift reports these cases rather than guessing.
