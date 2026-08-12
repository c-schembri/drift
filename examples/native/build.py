import sys
from pathlib import Path

from driftbuild.api import ArtifactSpec, BenchmarkSpec, ProjectApi, TestSpec


def project(api: ProjectApi):
    core = api.object_library("core", sources=api.files("src/add.c"), include_dirs=("include",))
    math = api.static_library(
        "math",
        public_headers=api.files("include/add.h"),
        include_dirs=("include",),
        objects=(core,),
    )
    hello = api.executable(
        "hello",
        sources=api.files("src/main.cpp"),
        dependencies=(api.private(math),),
    )
    assets = api.runtime_bundle("assets", api.files("assets/message.txt"), destination="bin")
    all_targets = api.alias("all", (hello, assets))
    executable = str(
        api.root
        / ".drift"
        / "build"
        / f"{sys.platform}-{api.config.architecture}-{api.config.compiler}-{api.config.build_type}"
        / "bin"
        / "hello"
    )
    if sys.platform == "win32":
        executable += ".exe"
    api.test(TestSpec("hello", (executable,), labels=("native", "smoke"), build_targets=(hello,)))
    api.benchmark(BenchmarkSpec("hello-startup", (executable,), build_targets=(hello,), warmups=1, repetitions=3))
    api.artifact(ArtifactSpec("hello", (api.output(hello), Path("assets/message.txt"))))
    return api.project("native-fixture", defaults=(all_targets,))
