import os
import sys
from pathlib import Path

import pytest

from driftbuild.api import BuildConfig
from driftbuild.autotools import project_import
from driftbuild.model import Dependency
from driftbuild.process import run


@pytest.mark.skipif(os.name == "nt", reason="Autotools adapter requires a POSIX host")
def test_autotools_import_exposes_staged_interface(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "include").mkdir(parents=True)
    (source / "sample.pc.in").write_text(
        "prefix=@prefix@\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n"
        "Name: sample\nVersion: 1\nLibs: -L${libdir} -lsample\nCflags: -I${includedir} -DSAMPLE_API\n",
        encoding="utf-8",
    )
    (source / "configure").write_text(
        "#!/bin/sh\nprintf 'all:\\n\\t@true\\ninstall:\\n\\t@true\\n' > Makefile\n",
        encoding="utf-8",
    )

    project = project_import(source, tmp_path / "state", BuildConfig(sys.platform), "sample")

    target = project.targets[0]
    assert target.kind == "external_library"
    assert target.action is not None
    assert target.outputs[0].name == ".drift-installed"
    interface = target.dependencies[0]
    assert isinstance(interface, Dependency)
    assert interface.compile.defines == ("SAMPLE_API",)
    assert interface.link.arguments == ("-lsample",)
    run(target.action.command, environment=target.action.environment, capture=True)
    assert target.outputs[0].is_file()
