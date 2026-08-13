# Dependency compatibility

These projects pin real upstream revisions and exercise Drift's normalized package boundary rather than mocks.

| Case | Adapter | Contract |
| --- | --- | --- |
| `zlib` | CMake | static linkage, component selection, public headers, link interface |
| `../examples/inih` | Meson | options, static library import, C consumer |
| `../examples/sdl3-window` | MSBuild/CMake | host adapter selection, large graph, runtime application |
| `autotools-yaml` | Autotools | generated configure import, staged install, C consumer |
| `pkg-config-zlib` | pkg-config | host interface normalization and C consumer |
| `vcpkg-zlib` | vcpkg | pinned registry baseline, binary package, C consumer |
| `conan-catch2` | Conan | pinned recipe, isolated graph and package, C++ consumer |

The compatibility workflow builds and runs each case on its supported hosts. Add a small consuming program whenever
an adapter or interface behavior is added; merely configuring an upstream project is not sufficient evidence.
The slower Conan and vcpkg cases run weekly and on demand. Make, B2, SCons, and prebuilt imports have deterministic
unit fixtures because their conventional host-tool availability varies substantially across runners.
