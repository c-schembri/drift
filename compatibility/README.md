# Dependency compatibility

These projects pin real upstream revisions and exercise Drift's normalized package boundary rather than mocks.

| Case | Adapter | Contract |
| --- | --- | --- |
| `zlib` | CMake | static linkage, component selection, public headers, link interface |
| `../examples/inih` | Meson | options, static library import, C consumer |
| `../examples/sdl3-window` | MSBuild/CMake | host adapter selection, large graph, runtime application |

The compatibility workflow builds and runs each case on its supported hosts. Add a small consuming program whenever
an adapter or interface behavior is added; merely configuring an upstream project is not sufficient evidence.
