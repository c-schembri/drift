from driftbuild.api import ProjectApi

VCPKG_BASELINE = "2273a28f34ce5aac8be50b3b6b44da7fc1722e06"


def project(api: ProjectApi):
    zlib = api.package("zlib", source=api.vcpkg("zlib", VCPKG_BASELINE))
    smoke = api.executable(
        "vcpkg-zlib-smoke",
        sources=api.files("src/main.c"),
        dependencies=(api.private(zlib),),
    )
    return api.project("vcpkg-zlib", defaults=(smoke,))
