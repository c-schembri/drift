from pathlib import Path

from driftbuild.pkgconfig import dependency_resolve
from driftbuild.process import ProcessResult


def test_pkg_config_normalizes_compile_and_link_flags(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DRIFT_PKG_CONFIG", "pkgconf")

    def fake_run(arguments, **_kwargs):  # type: ignore[no-untyped-def]
        output = "-IC:/sdk/include -DSAMPLE_STATIC -pthread\n" if "--cflags" in arguments else "-LC:/sdk/lib -lsample -pthread\n"
        return ProcessResult(tuple(arguments), 0, output, "")

    monkeypatch.setattr("driftbuild.pkgconfig.run", fake_run)

    dependency = dependency_resolve("sample", static=True)

    assert dependency.compile.include_dirs == (Path("C:/sdk/include"),)
    assert dependency.compile.defines == ("SAMPLE_STATIC",)
    assert dependency.compile.arguments == ("-pthread",)
    assert dependency.link.library_dirs == (Path("C:/sdk/lib"),)
    assert dependency.link.arguments == ("-lsample", "-pthread")

