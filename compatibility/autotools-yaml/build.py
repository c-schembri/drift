from driftbuild.api import ProjectApi


def project(api: ProjectApi):
    yaml = api.package(
        "yaml",
        source=api.archive(
            "https://pyyaml.org/download/libyaml/yaml-0.2.5.tar.gz",
            "c642ae9b75fee120b2d96c712538bd2cf283228d2337df2cf2988e3c02678ef4",
            strip_prefix="yaml-0.2.5",
        ),
        adapter="autotools",
    )
    smoke = api.executable(
        "autotools-yaml-smoke",
        sources=api.files("src/main.c"),
        dependencies=(api.private(yaml),),
    )
    return api.project("autotools-yaml", defaults=(smoke,))
