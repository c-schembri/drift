import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from driftbuild.api import BuildConfig, ProjectApi
from driftbuild.bootstrap import ninja_resolve
from driftbuild.errors import ConfigurationError
from driftbuild.graph import project_validate
from driftbuild.model import TargetDependency
from driftbuild.packages import (
    package_lock_create,
    package_lock_diff,
    package_lock_read,
    packages_compose,
    packages_fetch,
)


def _archive_create(path: Path, files: dict[str, str]) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_project(root: Path, archive: Path, checksum: str):
    overlay = root / "answer_overlay.py"
    overlay.write_text(
        """def project(api):
    answer = api.static_library(
        "answer",
        sources=api.files("src/answer.c"),
        public_headers=api.files("include/answer.h"),
        include_dirs=("include",),
    )
    return api.project("answer", defaults=(answer,))
""",
        encoding="utf-8",
    )
    api = ProjectApi(root, BuildConfig(sys.platform))
    package = api.package(
        "answer",
        source=api.archive(str(archive), checksum, strip_prefix="answer-1"),
        overlay="answer_overlay.py",
    )
    source = root / "main.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    app = api.executable("app", sources=api.files("main.c"), dependencies=(api.private(package),))
    return api.project("sample", defaults=(app,))


def test_archive_package_locks_fetches_and_composes_targets(tmp_path: Path) -> None:
    archive = tmp_path / "answer.zip"
    checksum = _archive_create(
        archive,
        {
            "answer-1/include/answer.h": "int answer(void);\n",
            "answer-1/src/answer.c": "int answer(void) { return 42; }\n",
        },
    )
    project = _package_project(tmp_path, archive, checksum)
    store = tmp_path / "store"

    package_lock_create(project, tmp_path, store)
    lock = package_lock_read(tmp_path)
    composed = packages_compose(project, tmp_path, BuildConfig(sys.platform), store_root=store, offline=True)
    targets = project_validate(composed)

    assert [package.name for package in lock.packages] == ["answer"]
    assert len(composed.targets) == 2
    package_target = next(target for target in composed.targets if target.name.startswith("__drift_package_answer_"))
    assert package_target.sources[0].is_absolute()  # type: ignore[union-attr]
    assert package_target.name in targets


def test_refresh_repairs_corrupt_cached_source(tmp_path: Path) -> None:
    archive = tmp_path / "answer.zip"
    checksum = _archive_create(archive, {"answer-1/value.txt": "original\n"})
    api = ProjectApi(tmp_path, BuildConfig(sys.platform))
    api.package("answer", source=api.archive(str(archive), checksum, strip_prefix="answer-1"), adapter="prebuilt")
    project = api.project("sample")
    store = tmp_path / "store"
    locked = package_lock_create(project, tmp_path, store)
    source = store / "sources" / locked.packages[0].content_sha256
    (source / "value.txt").write_text("corrupt\n", encoding="utf-8")

    refreshed = package_lock_create(project, tmp_path, store, refresh=True)

    assert refreshed.packages[0].content_sha256 == locked.packages[0].content_sha256
    assert (source / "value.txt").read_text(encoding="utf-8") == "original\n"


def test_changed_overlay_makes_lock_stale(tmp_path: Path) -> None:
    archive = tmp_path / "answer.zip"
    checksum = _archive_create(archive, {"answer-1/source.c": "int answer(void) { return 42; }\n"})
    project = _package_project(tmp_path, archive, checksum)
    store = tmp_path / "store"
    package_lock_create(project, tmp_path, store)
    (tmp_path / "answer_overlay.py").write_text("def project(api): return api.project('changed')\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="stale"):
        packages_fetch(project, tmp_path, store_root=store, offline=True)

    archive.unlink()
    refreshed = package_lock_create(project, tmp_path, store)
    assert refreshed.packages[0].content_sha256


def test_package_can_supply_its_own_drift_provider(tmp_path: Path) -> None:
    archive = tmp_path / "native.zip"
    checksum = _archive_create(
        archive,
        {
            "native/drift.toml": '[project]\napi-version = 0\nprovider = "build:project"\n',
            "native/build.py": """def project(api):
    library = api.static_library("native", sources=api.files("native.c"))
    return api.project("native", defaults=(library,))
""",
            "native/native.c": "int native_value(void) { return 1; }\n",
        },
    )
    api = ProjectApi(tmp_path, BuildConfig(sys.platform))
    package = api.package("native", source=api.archive(str(archive), checksum, strip_prefix="native"))
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    app = api.executable("app", sources=api.files("main.c"), dependencies=(api.private(package.target("native")),))
    project = api.project("sample", defaults=(app,))
    store = tmp_path / "store"

    package_lock_create(project, tmp_path, store)
    composed = packages_compose(project, tmp_path, BuildConfig(sys.platform), store_root=store, offline=True)

    assert any(target.name.startswith("__drift_package_native_") for target in composed.targets)


def test_drift_package_can_lock_and_compose_transitive_packages(tmp_path: Path) -> None:
    child_archive = tmp_path / "child.zip"
    child_checksum = _archive_create(
        child_archive,
        {
            "child/drift.toml": '[project]\napi-version = 0\nprovider = "build:project"\n',
            "child/build.py": """def project(api):
    child = api.static_library("child", sources=api.files("child.c"))
    return api.project("child", defaults=(child,))
""",
            "child/child.c": "int child(void) { return 7; }\n",
        },
    )
    parent_archive = tmp_path / "parent.zip"
    parent_build = f'''def project(api):
    child = api.package(
        "child",
        source=api.archive("child.zip", "{child_checksum}", strip_prefix="child"),
    )
    parent = api.static_library(
        "parent",
        sources=api.files("parent.c"),
        dependencies=(api.private(child),),
    )
    return api.project("parent", defaults=(parent,))
'''
    with zipfile.ZipFile(parent_archive, "w") as archive:
        archive.writestr("parent/drift.toml", '[project]\napi-version = 0\nprovider = "build:project"\n')
        archive.writestr("parent/build.py", parent_build)
        archive.writestr("parent/parent.c", "int parent(void) { return 42; }\n")
        archive.writestr("parent/child.zip", child_archive.read_bytes())
    parent_checksum = hashlib.sha256(parent_archive.read_bytes()).hexdigest()
    api = ProjectApi(tmp_path, BuildConfig(sys.platform))
    parent = api.package(
        "parent",
        source=api.archive(str(parent_archive), parent_checksum, strip_prefix="parent"),
    )
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    app = api.executable("app", sources=api.files("main.c"), dependencies=(api.private(parent),))
    project = api.project("sample", defaults=(app,))
    store = tmp_path / "store"

    lock = package_lock_create(project, tmp_path, store)
    composed = packages_compose(project, tmp_path, BuildConfig(sys.platform), store_root=store, offline=True)

    assert [(item.scope, item.name) for item in lock.packages] == [("", "parent"), ("parent", "child")]
    package_targets = [target for target in composed.targets if target.name.startswith("__drift_package_")]
    assert len(package_targets) == 2
    parent_target = next(target for target in package_targets if "parent_parent" in target.name)
    child_target = next(target for target in package_targets if "parent_child_child" in target.name)
    assert any(
        isinstance(dependency, TargetDependency) and dependency.target.name == child_target.name
        for dependency in parent_target.dependencies
    )


def test_archive_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    checksum = _archive_create(archive, {"../outside.txt": "nope"})
    api = ProjectApi(tmp_path, BuildConfig(sys.platform))
    package = api.package("unsafe", source=api.archive(str(archive), checksum), overlay=None)
    project = api.project("sample")
    assert package.name == "unsafe"

    with pytest.raises(ConfigurationError, match="escapes"):
        package_lock_create(project, tmp_path, tmp_path / "store")
    assert not (tmp_path / "outside.txt").exists()


def test_archive_files_receive_one_deterministic_timestamp(tmp_path: Path) -> None:
    archive = tmp_path / "ordered.zip"
    checksum = _archive_create(
        archive,
        {
            "ordered/generated": "generated\n",
            "ordered/source": "source\n",
        },
    )
    api = ProjectApi(tmp_path, BuildConfig(sys.platform))
    api.package(
        "ordered",
        source=api.archive(str(archive), checksum, strip_prefix="ordered"),
        adapter="prebuilt",
    )

    lock = package_lock_create(api.project("sample"), tmp_path, tmp_path / "store")
    source = tmp_path / "store" / "sources" / lock.packages[0].content_sha256

    assert (source / "generated").stat().st_mtime_ns == (source / "source").stat().st_mtime_ns


def test_git_package_records_exact_tree(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is unavailable")
    source = tmp_path / "source"
    source.mkdir()
    (source / "value.txt").write_text("42\n", encoding="utf-8")
    subprocess.run((git, "init", "--quiet"), cwd=source, check=True)
    subprocess.run((git, "add", "value.txt"), cwd=source, check=True)
    subprocess.run(
        (
            git,
            "-c",
            "user.name=Drift Test",
            "-c",
            "user.email=drift@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ),
        cwd=source,
        check=True,
    )
    revision = subprocess.run(
        (git, "rev-parse", "HEAD"), cwd=source, check=True, capture_output=True, text=True
    ).stdout.strip()
    root = tmp_path / "project"
    root.mkdir()
    api = ProjectApi(root, BuildConfig(sys.platform))
    api.package("source", source=api.git(str(source), revision))

    lock = package_lock_create(api.project("sample"), root, tmp_path / "store")

    cached = tmp_path / "store" / "sources" / lock.packages[0].content_sha256
    assert (cached / "value.txt").read_text(encoding="utf-8") == "42\n"
    assert not (cached / ".git").exists()


def test_tracked_git_package_reports_and_updates_remote_ref(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is unavailable")
    source = tmp_path / "source"
    source.mkdir()
    (source / "value.txt").write_text("one\n", encoding="utf-8")
    subprocess.run((git, "init", "--quiet"), cwd=source, check=True)
    subprocess.run((git, "add", "value.txt"), cwd=source, check=True)
    commit = (git, "-c", "user.name=Drift Test", "-c", "user.email=drift@example.invalid", "commit", "--quiet")
    subprocess.run((*commit, "-m", "first"), cwd=source, check=True)
    first = subprocess.run(
        (git, "rev-parse", "HEAD"), cwd=source, check=True, capture_output=True, text=True
    ).stdout.strip()
    branch = subprocess.run(
        (git, "branch", "--show-current"), cwd=source, check=True, capture_output=True, text=True
    ).stdout.strip()
    root = tmp_path / "project"
    root.mkdir()
    api = ProjectApi(root, BuildConfig(sys.platform))
    api.package("source", source=api.git(str(source), first, track=f"refs/heads/{branch}"))
    project = api.project("sample")
    store = tmp_path / "store"
    locked = package_lock_create(project, root, store)
    (source / "value.txt").write_text("two\n", encoding="utf-8")
    subprocess.run((git, "add", "value.txt"), cwd=source, check=True)
    subprocess.run((*commit, "-m", "second"), cwd=source, check=True)

    candidate = package_lock_create(project, root, store, refresh=True, write=False)
    changes = package_lock_diff(locked, candidate)
    updated = package_lock_create(project, root, store, refresh=True)
    fetched = packages_fetch(project, root, store_root=store)

    assert changes and changes[0].startswith("~ source ")
    assert updated.packages[0].source.revision != first  # type: ignore[union-attr]
    assert (fetched["source"] / "value.txt").read_text(encoding="utf-8") == "two\n"


def test_cli_builds_and_runs_archive_package(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    archive = project_root / "answer.zip"
    checksum = _archive_create(
        archive,
        {
            "answer-1/include/answer.h": "int answer(void);\n",
            "answer-1/src/answer.c": '#include "answer.h"\nint answer(void) { return 42; }\n',
        },
    )
    (project_root / "answer_overlay.py").write_text(
        """def project(api):
    answer = api.static_library(
        "answer",
        sources=api.files("src/answer.c"),
        public_headers=api.files("include/answer.h"),
        include_dirs=("include",),
    )
    return api.project("answer", defaults=(answer,))
""",
        encoding="utf-8",
    )
    (project_root / "main.c").write_text(
        '#include "answer.h"\n#include <stdio.h>\nint main(void) { printf("%d\\n", answer()); return 0; }\n',
        encoding="utf-8",
    )
    (project_root / "build.py").write_text(
        f'''def project(api):
    answer = api.package(
        "answer",
        source=api.archive("answer.zip", "{checksum}", strip_prefix="answer-1"),
        overlay="answer_overlay.py",
    )
    app = api.executable(
        "app",
        sources=api.files("main.c"),
        dependencies=(api.private(answer.target("answer")),),
    )
    return api.project("package-fixture", defaults=(app,))
''',
        encoding="utf-8",
    )
    (project_root / "drift.toml").write_text(
        '[project]\napi-version = 0\nprovider = "build:project"\n', encoding="utf-8"
    )
    repository = Path(__file__).parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repository)
    environment["DRIFT_HOME"] = str(tmp_path / "drift-home")
    environment["DRIFT_NINJA"] = str(ninja_resolve(repository / ".drift"))

    locked = subprocess.run(
        (sys.executable, "-m", "driftbuild", "--root", str(project_root), "lock"),
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert locked.returncode == 0, locked.stdout + locked.stderr
    executed = subprocess.run(
        (sys.executable, "-m", "driftbuild", "--root", str(project_root), "--offline", "run"),
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    assert "Build timing: total " in executed.stdout
    assert "42" in executed.stdout
