# Migration from project scripts

Drift is motivated by Castalia's project tooling, but its first milestone is intentionally standalone. Nothing in this repository imports Castalia, modifies its checkout, or assumes its source layout.

Move one concept at a time from imperative orchestration to declarations. A typical script starts by collecting sources and spelling compiler commands itself:

```python
sources = glob("source/**/*.cpp")
run([compiler, *flags, *sources, *libraries, "-o", output])
copy_runtime_files(output_directory)
```

The equivalent Drift provider states ownership and interfaces, leaving scheduling to Ninja:

```python
sources = api.tree("source", include=("**/*.cpp",), exclude=("generated/**",))
engine = api.static_library(
    "engine",
    sources=sources,
    public_headers=api.tree("include", include=("**/*.h",)),
    include_dirs=("include",),
    dependencies=(api.dependency("sdl", include_dirs=("vendor/sdl/include",), libraries=("SDL3",)),),
)
app = api.executable("app", sources=api.files("app/main.cpp"), dependencies=(api.private(engine),))
runtime = api.runtime_bundle("runtime", api.files("assets/default.json"), destination="bin")
return api.project("app", defaults=(app, runtime))
```

Before adopting Drift in another repository, reproduce that repository's current target inventory as a Drift fixture, compare outputs and command lines, and measure clean, incremental, and no-op builds. Integration launchers or CI changes belong to a later migration project, not Drift 0.1.
