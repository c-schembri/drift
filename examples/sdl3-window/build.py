from driftbuild.api import ProjectApi

SDL_REVISION = "8e37db5e797b6167f3a00d697d816a684bd259c7"


def project(api: ProjectApi):
    sdl3 = api.package(
        "sdl3",
        source=api.git("https://github.com/libsdl-org/SDL.git", SDL_REVISION),
    )
    window = api.executable(
        "sdl3-window",
        sources=api.files("src/main.c"),
        dependencies=(api.private(sdl3),),
        link_arguments=("/SUBSYSTEM:WINDOWS",) if api.config.platform == "win32" else (),
    )
    return api.project("sdl3-window", defaults=(window,))
