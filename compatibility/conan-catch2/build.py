from driftbuild.api import ProjectApi

CATCH2_REVISION = "b3fb4b9feafcd8d91c5cb510a4775143fdbef02f"


def project(api: ProjectApi):
    catch2 = api.package(
        "catch2",
        source=api.git("https://github.com/catchorg/Catch2.git", CATCH2_REVISION),
    )
    smoke = api.executable(
        "conan-catch2-smoke",
        sources=api.files("src/main.cpp"),
        dependencies=(api.private(catch2),),
    )
    return api.project("conan-catch2", defaults=(smoke,))
