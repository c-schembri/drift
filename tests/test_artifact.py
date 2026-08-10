import hashlib
from pathlib import Path

from driftbuild.artifact import artifacts_create
from driftbuild.model import ArtifactSpec, ProjectSpec


def test_zip_is_reproducible(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("one\n", encoding="utf-8")
    project = ProjectSpec("sample", artifacts=(ArtifactSpec("sample", (Path("one.txt"),)),))
    state = tmp_path / ".drift"

    first = artifacts_create(project, tmp_path, state, {})[0]
    first_hash = hashlib.sha256(first.read_bytes()).hexdigest()
    second = artifacts_create(project, tmp_path, state, {})[0]

    assert hashlib.sha256(second.read_bytes()).hexdigest() == first_hash
