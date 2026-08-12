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
    app = api.executable(
        "app",
        sources=api.files("src/main.c"),
        dependencies=(api.private(zlib.target("zlibstatic")),),
    )
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

An upstream Visual C++ project can be consumed without a handwritten overlay or an MSBuild invocation:

```python
sdl = api.package(
    "sdl3",
    source=api.git("https://github.com/libsdl-org/SDL.git", revision="0123456789abcdef0123456789abcdef01234567"),
    build=api.msbuild("VisualC/SDL/SDL.vcxproj", kind="static_library", defines=("SDL_STATIC_LIB",)),
)
```

The importer selects the requested Debug/Release architecture, source and header items, include paths, definitions,
compiler options, disabled warnings, libraries, and native target kind. A target-kind override supports upstreams that
only ship a shared-library project but can also be compiled statically.

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
- Package build providers and MSBuild descriptions are evaluated only after locked content is materialized. Local
  overlays remain available when an upstream cannot be described by a supported importer.
- The first package format supports a flat set of source packages containing ordinary native targets. Transitive package
  declarations, custom-action package targets, registries, version constraints, and prebuilt package variants are not
  supported yet; Drift reports these cases rather than silently guessing.
