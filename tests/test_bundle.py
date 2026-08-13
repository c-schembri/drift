import json
import sys
from pathlib import Path

import pytest

from driftbuild.bundle import main


def test_bundle_removes_only_previously_owned_stale_files(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("current", encoding="utf-8")
    destination = tmp_path / "runtime"
    destination.mkdir()
    (destination / "stale.txt").write_text("stale", encoding="utf-8")
    (destination / "unowned.txt").write_text("keep", encoding="utf-8")
    stamp = destination / ".runtime.stamp"
    manifest = Path(str(stamp) + ".json")
    manifest.write_text('["stale.txt"]\n', encoding="utf-8")
    spec = tmp_path / "bundle.json"
    spec.write_text(
        json.dumps(
            {
                "entries": [{"source": str(source), "destination": "nested/current.txt"}],
                "destination": str(destination),
                "stamp": str(stamp),
                "clean": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["driftbuild.bundle", "--spec", str(spec)])

    assert main() == 0
    assert not (destination / "stale.txt").exists()
    assert (destination / "unowned.txt").is_file()
    assert (destination / "nested" / "current.txt").read_text(encoding="utf-8") == "current"


def test_bundle_rejects_stale_manifest_paths_outside_destination(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "runtime"
    destination.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    stamp = destination / ".assets.stamp"
    manifest = Path(str(stamp) + ".json")
    manifest.write_text('["../outside.txt"]\n', encoding="utf-8")
    spec = tmp_path / "bundle.json"
    spec.write_text(
        json.dumps({"destination": str(destination), "entries": [], "stamp": str(stamp), "clean": True}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["driftbuild.bundle", "--spec", str(spec)])

    with pytest.raises(ValueError, match="Invalid runtime bundle destination"):
        main()

    assert outside.read_text(encoding="utf-8") == "keep"
