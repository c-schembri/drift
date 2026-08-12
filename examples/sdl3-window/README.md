# SDL3 Window

This example locks SDL 3.4.10 to its exact upstream Git commit. Drift detects SDL's upstream build description,
imports its target graph, and selects the SDL3 library without a package-specific recipe.

```console
drift lock
drift run
```

Close the window normally to stop the program. For automated smoke checks, pass a lifetime in milliseconds:

```console
drift run -- --timeout-ms 1000
```

On Windows, Drift imports SDL's checked-in Visual C++ project directly. On Linux and macOS it uses CMake's File API,
caches the configured graph, and delegates only the dependency build to CMake.
