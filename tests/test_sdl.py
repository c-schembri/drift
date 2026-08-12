from pathlib import Path

from driftbuild.api import BuildConfig
from driftbuild.model import Dependency
from driftbuild.sdl import project_import


def _fixture(root: Path) -> None:
    for relative in (
        "include/SDL3/SDL.h",
        "src/SDL.c",
        "src/video/x11/SDL_x11video.c",
        "src/video/cocoa/SDL_cocoavideo.m",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def test_linux_recipe_selects_x11_sources_and_libraries(tmp_path: Path) -> None:
    _fixture(tmp_path)

    project = project_import(tmp_path, BuildConfig("linux", compiler="gcc"))
    target = project.targets[0]

    assert Path("src/video/x11/SDL_x11video.c") in target.sources
    assert Path("src/video/cocoa/SDL_cocoavideo.m") not in target.sources
    assert "SDL_VIDEO_DRIVER_X11=1" in target.defines
    assert isinstance(target.dependencies[0], Dependency)
    assert "-lX11" in target.dependencies[0].link.arguments


def test_macos_recipe_selects_cocoa_sources_and_frameworks(tmp_path: Path) -> None:
    _fixture(tmp_path)

    project = project_import(tmp_path, BuildConfig("darwin", compiler="clang"))
    target = project.targets[0]

    assert Path("src/video/cocoa/SDL_cocoavideo.m") in target.sources
    assert Path("src/video/x11/SDL_x11video.c") not in target.sources
    assert isinstance(target.dependencies[0], Dependency)
    assert "Cocoa" in target.dependencies[0].link.arguments
