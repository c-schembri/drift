from driftbuild.api import ProjectApi


def project(api: ProjectApi):
    library = api.static_library(
        "cross-smoke",
        sources=api.files("src/smoke.c"),
        public_headers=api.files("include/smoke.h"),
        include_dirs=("include",),
    )
    return api.project("cross-smoke", defaults=(library,))
