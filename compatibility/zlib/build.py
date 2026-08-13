from driftbuild.api import ProjectApi

ZLIB_REVISION = "51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf"


def project(api: ProjectApi):
    zlib = api.package(
        "zlib",
        source=api.git("https://github.com/madler/zlib.git", ZLIB_REVISION),
        components=("zlibstatic",),
        linkage="static",
    )
    smoke = api.executable(
        "zlib-smoke",
        sources=api.files("src/main.c"),
        dependencies=(api.private(zlib),),
    )
    return api.project("zlib-compatibility", defaults=(smoke,))
