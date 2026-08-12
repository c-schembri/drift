# Visual Studio

Drift can project a native target graph into a Visual Studio solution while retaining Ninja as the only build backend:

```console
drift generate visual-studio [project-directory]
```

The default output is `.drift/visual-studio/<project>.sln`. Use `--output PATH` to select another directory and `--startup-target NAME` to place a specific executable first in the solution.

Each Drift target receives a Makefile-style `.vcxproj` and a `.vcxproj.filters` file. Projects contain the declared sources and headers, stable target GUIDs, dependency references, Debug and Release configurations, propagated include paths and definitions, target-specific build/clean/rebuild commands, and debugger settings for executables.

The solution also contains one `<project> (build)` project. It is the only project selected for solution-wide builds, so Visual Studio launches one Drift/Ninja process rather than racing a separate Ninja process per target. Individual target projects remain directly buildable from Solution Explorer.

Generated files are deterministic and should not be committed. Regenerate after changing target names, sources, headers, dependencies, or provider-controlled configuration. Visual Studio must be able to find the `drift` command in its environment.

Visual Studio projects are an IDE frontend, not an MSBuild translation. Command-line builds, Visual Studio builds, tests, and release automation therefore share the same Drift graph and Ninja behavior.
