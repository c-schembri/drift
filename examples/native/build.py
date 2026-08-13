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
    generated_message = api.custom_target(
        "generated-message",
        api.provider_action(
            "codegen:generate_message",
            ("{out}",),
            outputs=("generated/message.txt",),
            description="GENERATE message.txt",
            restat=True,
        ),
    )
    assets = api.runtime_bundle(
        "assets",
        (api.deploy(api.output(generated_message), "message.txt"),),
        destination="bin",
    )
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
    api.test(TestSpec("hello", target=hello, labels=("native", "smoke")))
    api.benchmark(BenchmarkSpec("hello-startup", (executable,), build_targets=(hello,), warmups=1, repetitions=3))
    api.artifact(ArtifactSpec("hello", (api.output(hello), Path("assets/message.txt"))))
    return api.project("native-fixture", defaults=(all_targets,))
