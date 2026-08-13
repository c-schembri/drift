from pathlib import Path

from driftbuild.api import FileLock, copy_file, copy_tree_contents, outputs_current


def test_provider_filesystem_services_copy_changed_trees(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    (source / "nested").mkdir(parents=True)
    input_path = source / "nested/input.txt"
    input_path.write_text("value", encoding="utf-8")

    copy_tree_contents(source, destination)
    output = destination / "nested/input.txt"
    assert output.read_text(encoding="utf-8") == "value"
    assert outputs_current((output,), (input_path,))

    direct = tmp_path / "direct.txt"
    copy_file(input_path, direct)
    assert direct.read_text(encoding="utf-8") == "value"


def test_provider_file_lock_supports_nonblocking_contention(tmp_path: Path) -> None:
    path = tmp_path / "provider.lock"
    first = FileLock(path)
    second = FileLock(path)
    assert first.acquire(False)
    try:
        assert not second.acquire(False)
    finally:
        first.release()
    assert second.acquire(False)
    second.release()
    assert path.stat().st_size > 0
