# SDL3 Win32 Window

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

The example is currently Windows-only. Its local overlay translates SDL's checked-in `VisualC/SDL/SDL.vcxproj` source
inventory into a Drift static-library target and exposes SDL's headers and required Win32 system libraries.
