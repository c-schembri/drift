from driftbuild.api import ProjectApi

INIH_REVISION = "577ae2dee1f0d9c2d11c7f10375c1715f3d6940c"


def project(api: ProjectApi):
    inih = api.package(
        "inih",
        source=api.git("https://github.com/benhoyt/inih.git", INIH_REVISION),
        adapter="meson",
        options={"tests": False, "with_INIReader": False},
        linkage="static",
    )
    example = api.executable(
        "inih-example",
        sources=api.files("src/main.c"),
        dependencies=(api.private(inih),),
    )
    return api.project("inih-example", defaults=(example,))
