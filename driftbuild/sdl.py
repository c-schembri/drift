"""Native SDL source recipes selected by host platform."""

from __future__ import annotations

from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.model import BuildConfig, ProjectSpec
from driftbuild.project import ProjectApi

_COMMON = (
    "src/*.c",
    "src/atomic/*.c",
    "src/audio/*.c",
    "src/camera/*.c",
    "src/core/*.c",
    "src/cpuinfo/*.c",
    "src/dialog/*.c",
    "src/dynapi/*.c",
    "src/events/*.c",
    "src/filesystem/*.c",
    "src/gpu/*.c",
    "src/haptic/*.c",
    "src/hidapi/*.c",
    "src/io/*.c",
    "src/io/generic/*.c",
    "src/joystick/*.c",
    "src/libm/*.c",
    "src/loadso/*.c",
    "src/locale/*.c",
    "src/main/*.c",
    "src/misc/*.c",
    "src/power/*.c",
    "src/process/*.c",
    "src/render/*.c",
    "src/render/*/*.c",
    "src/sensor/*.c",
    "src/stdlib/*.c",
    "src/storage/*.c",
    "src/thread/*.c",
    "src/time/*.c",
    "src/timer/*.c",
    "src/tray/*.c",
    "src/video/*.c",
    "src/video/yuv2rgb/*.c",
)

_UNIX = (
    "src/audio/disk/*.c",
    "src/audio/dummy/*.c",
    "src/camera/dummy/*.c",
    "src/core/unix/*.c",
    "src/dialog/dummy/*.c",
    "src/filesystem/posix/*.c",
    "src/haptic/dummy/*.c",
    "src/joystick/dummy/*.c",
    "src/joystick/virtual/*.c",
    "src/loadso/dlopen/*.c",
    "src/process/posix/*.c",
    "src/sensor/dummy/*.c",
    "src/storage/generic/*.c",
    "src/thread/pthread/*.c",
    "src/time/unix/*.c",
    "src/timer/unix/*.c",
    "src/video/dummy/*.c",
    "src/video/offscreen/*.c",
)


def _sources(root: Path, patterns: tuple[str, ...]) -> tuple[Path, ...]:
    files = {path.relative_to(root) for pattern in patterns for path in root.glob(pattern) if path.is_file()}
    return tuple(sorted(files, key=Path.as_posix))


def _linux_project(root: Path, config: BuildConfig) -> ProjectSpec:
    api = ProjectApi(root, config)
    sources = _sources(
        root,
        _COMMON
        + _UNIX
        + (
            "src/core/linux/SDL_evdev_capabilities.c",
            "src/core/linux/SDL_threadprio.c",
            "src/filesystem/unix/*.c",
            "src/locale/unix/*.c",
            "src/misc/unix/*.c",
            "src/power/linux/*.c",
            "src/storage/steam/*.c",
            "src/tray/dummy/*.c",
            "src/video/x11/*.c",
        ),
    )
    definitions = (
        "SDL_build_config_h_",
        "SDL_STATIC_LIB",
        "HAVE_GCC_ATOMICS=1",
        "HAVE_GCC_SYNC_LOCK_TEST_AND_SET=1",
        "HAVE_STDARG_H=1",
        "HAVE_STDDEF_H=1",
        "HAVE_STDINT_H=1",
        "HAVE_FLOAT_H=1",
        "HAVE_LIMITS_H=1",
        "HAVE_MATH_H=1",
        "HAVE_SIGNAL_H=1",
        "HAVE_STDIO_H=1",
        "HAVE_STDLIB_H=1",
        "HAVE_STRING_H=1",
        "HAVE_WCHAR_H=1",
        "SDL_AUDIO_DRIVER_DISK=1",
        "SDL_AUDIO_DRIVER_DUMMY=1",
        "SDL_CAMERA_DRIVER_DUMMY=1",
        "SDL_DIALOG_DUMMY=1",
        "SDL_FILESYSTEM_UNIX=1",
        "SDL_FSOPS_POSIX=1",
        "SDL_HAPTIC_DUMMY=1",
        "SDL_JOYSTICK_DUMMY=1",
        "SDL_JOYSTICK_VIRTUAL=1",
        "SDL_LOADSO_DLOPEN=1",
        "SDL_POWER_LINUX=1",
        "SDL_PROCESS_POSIX=1",
        "SDL_SENSOR_DUMMY=1",
        "SDL_STORAGE_GENERIC=1",
        "SDL_THREAD_PTHREAD=1",
        "SDL_TIME_UNIX=1",
        "SDL_TIMER_UNIX=1",
        "SDL_TRAY_DUMMY=1",
        "SDL_VIDEO_DRIVER_DUMMY=1",
        "SDL_VIDEO_DRIVER_OFFSCREEN=1",
        "SDL_VIDEO_DRIVER_X11=1",
    )
    system = api.dependency(
        "linux-desktop",
        link_arguments=("-ldl", "-lpthread", "-lm", "-lX11", "-lXext"),
    )
    target = api.static_library(
        "SDL3",
        sources=sources,
        public_headers=api.tree("include/SDL3", include=("*.h",)),
        include_dirs=("include", "include/build_config", "src"),
        defines=definitions,
        dependencies=(system,),
    )
    return api.project("SDL3", defaults=(target,))


def _macos_project(root: Path, config: BuildConfig) -> ProjectSpec:
    api = ProjectApi(root, config)
    sources = _sources(
        root,
        _COMMON
        + _UNIX
        + (
            "src/audio/coreaudio/*.m",
            "src/camera/coremedia/*.m",
            "src/dialog/cocoa/*.m",
            "src/filesystem/cocoa/*.m",
            "src/haptic/darwin/*.c",
            "src/hidapi/mac/*.c",
            "src/joystick/apple/*.m",
            "src/joystick/darwin/*.c",
            "src/locale/macos/*.m",
            "src/misc/macos/*.m",
            "src/power/macos/*.c",
            "src/storage/steam/*.c",
            "src/tray/cocoa/*.m",
            "src/video/cocoa/*.m",
        ),
    )
    frameworks = (
        "-framework",
        "AudioToolbox",
        "-framework",
        "AVFoundation",
        "-framework",
        "Carbon",
        "-framework",
        "Cocoa",
        "-framework",
        "CoreAudio",
        "-framework",
        "CoreVideo",
        "-framework",
        "ForceFeedback",
        "-framework",
        "Foundation",
        "-framework",
        "IOKit",
        "-framework",
        "Metal",
        "-framework",
        "QuartzCore",
        "-weak_framework",
        "UniformTypeIdentifiers",
    )
    system = api.dependency("macos-desktop", link_arguments=("-ldl", "-lpthread", *frameworks))
    target = api.static_library(
        "SDL3",
        sources=sources,
        public_headers=api.tree("include/SDL3", include=("*.h",)),
        include_dirs=("include", "include/build_config", "src"),
        defines=("SDL_STATIC_LIB",),
        compile_arguments=("-fobjc-weak",),
        dependencies=(system,),
    )
    return api.project("SDL3", defaults=(target,))


def project_import(root: Path, config: BuildConfig) -> ProjectSpec:
    """Import SDL using the native recipe for the selected platform."""
    if config.platform == "win32":
        from driftbuild.model import MsbuildProject
        from driftbuild.msbuild import project_import as msbuild_import

        return msbuild_import(
            root,
            config,
            MsbuildProject(Path("VisualC/SDL/SDL.vcxproj"), "static_library", ("SDL_STATIC_LIB",)),
        )
    if config.platform == "linux":
        return _linux_project(root, config)
    if config.platform == "darwin":
        return _macos_project(root, config)
    raise ConfigurationError(f"SDL package import does not support platform {config.platform}")
