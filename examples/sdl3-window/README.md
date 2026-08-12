# SDL3 Window

This example locks SDL 3.4.10 to its exact upstream Git commit and builds SDL's official Visual Studio source inventory
directly through Drift and Ninja. It does not invoke CMake or require a system SDL installation.

```console
drift lock
drift run
```

Close the window normally to stop the program. For automated smoke checks, pass a lifetime in milliseconds:

```console
drift run -- --timeout-ms 1000
```

Drift selects SDL's native Windows, Linux, or macOS recipe for the current host. Neither CMake nor MSBuild is invoked.
