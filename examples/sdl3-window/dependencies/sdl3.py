import xml.etree.ElementTree as element_tree
from pathlib import Path

from driftbuild.api import ProjectApi

MSBUILD_NAMESPACE = {"msbuild": "http://schemas.microsoft.com/developer/msbuild/2003"}


def _visual_studio_sources(root: Path) -> tuple[Path, ...]:
    project_file = root / "VisualC" / "SDL" / "SDL.vcxproj"
    document = element_tree.parse(project_file)
    project_directory = project_file.parent
    sources = {
        (project_directory / element.attrib["Include"]).resolve().relative_to(root)
        for element in document.findall(".//msbuild:ClCompile", MSBUILD_NAMESPACE)
        if "Include" in element.attrib
    }
    return tuple(sorted(sources, key=lambda path: path.as_posix()))


def project(api: ProjectApi):
    windows = api.dependency(
        "windows-sdk",
        link_arguments=(
            "kernel32.lib",
            "user32.lib",
            "gdi32.lib",
            "winmm.lib",
            "imm32.lib",
            "ole32.lib",
            "oleaut32.lib",
            "version.lib",
            "uuid.lib",
            "advapi32.lib",
            "setupapi.lib",
            "shell32.lib",
        ),
    )
    sdl3 = api.static_library(
        "SDL3-static",
        sources=api.files(*_visual_studio_sources(api.root)),
        public_headers=api.tree("include/SDL3", include=("*.h",)),
        include_dirs=("include", "include/build_config", "src", "src/core/windows"),
        defines=("SDL_STATIC_LIB",),
        compile_arguments=("/utf-8", "/wd4100", "/wd4127", "/wd4152", "/wd4201"),
        dependencies=(windows,),
    )
    return api.project("sdl3", defaults=(sdl3,))
