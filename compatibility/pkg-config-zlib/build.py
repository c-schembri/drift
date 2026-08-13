from driftbuild.api import ProjectApi


def project(api: ProjectApi):
    zlib = api.pkg_config("zlib")
    smoke = api.executable(
        "pkg-config-zlib-smoke",
        sources=api.files("src/main.c"),
        dependencies=(zlib,),
    )
    return api.project("pkg-config-zlib", defaults=(smoke,))
