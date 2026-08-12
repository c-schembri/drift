import shutil
import sys
from pathlib import Path

from driftbuild.api import BuildConfig
from driftbuild.bootstrap import conan_resolve, meson_resolve, ninja_resolve
from driftbuild.conan import project_import


def test_conan_recipe_imports_packaged_interface(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "source"
    (source / "include").mkdir(parents=True)
    (source / "include" / "sample.h").write_text("#define SAMPLE_VALUE 42\n", encoding="utf-8")
    (source / "conanfile.py").write_text(
        """from conan import ConanFile
from conan.tools.files import copy


class Sample(ConanFile):
    name = "sample"
    version = "1.0"
    package_type = "header-library"
    exports_sources = "include/*"

    def package(self):
        copy(self, "*.h", self.source_folder, self.package_folder)

    def package_id(self):
        self.info.clear()
""",
        encoding="utf-8",
    )
    repository = Path(__file__).parents[1]
    monkeypatch.setenv("DRIFT_CONAN", str(conan_resolve(repository / ".drift").resolve()))
    monkeypatch.setenv("DRIFT_MESON", str(meson_resolve(repository / ".drift").resolve()))
    monkeypatch.setenv("DRIFT_NINJA", str(ninja_resolve(repository / ".drift").resolve()))
    cmake = shutil.which("cmake")
    if cmake is not None:
        monkeypatch.setenv("DRIFT_CMAKE", cmake)

    project = project_import(source, tmp_path / "state", BuildConfig(sys.platform), "sample")

    target = project.targets[0]
    assert project.defaults[0].name == "sample"
    assert target.kind == "external_library"
    assert target.action is not None
    assert (target.include_dirs[0] / "sample.h").is_file()
    assert target.outputs[0].name == ".drift-installed"
