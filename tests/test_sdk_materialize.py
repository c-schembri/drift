import json
from pathlib import Path

import pytest

from driftbuild.errors import ConfigurationError
from driftbuild.model import LocalSdkSpec
from driftbuild.sdk_materialize import sdk_materialize, sdks_materialize


def _spec(tmp_path: Path, payload: object) -> LocalSdkSpec:
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "vendor/sdk"
    descriptor = tmp_path / "sdk.json"
    descriptor.write_text(json.dumps(payload), encoding="utf-8")
    return LocalSdkSpec("sample", source, destination, descriptor)


def test_materialize_replaces_destination_with_required_and_optional_files(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        {"materialize": {"required": ["include"], "optional": ["LICENSE*", "missing*"]}},
    )
    (spec.source / "include/nested").mkdir(parents=True)
    (spec.source / "include/nested/header.h").write_text("header", encoding="utf-8")
    (spec.source / "LICENSE.txt").write_text("license", encoding="utf-8")
    spec.destination.mkdir(parents=True)
    (spec.destination / "stale.txt").write_text("stale", encoding="utf-8")

    assert sdk_materialize(spec) == 2
    assert (spec.destination / "include/nested/header.h").read_text(encoding="utf-8") == "header"
    assert (spec.destination / "LICENSE.txt").is_file()
    assert not (spec.destination / "stale.txt").exists()


def test_materialize_rejects_missing_required_patterns(tmp_path: Path) -> None:
    spec = _spec(tmp_path, {"materialize": {"required": ["include"]}})

    with pytest.raises(ConfigurationError, match="matched no files"):
        sdk_materialize(spec)


def test_materialize_selects_declared_names(tmp_path: Path) -> None:
    spec = _spec(tmp_path, {"materialize": {"required": ["header.h"]}})
    (spec.source / "header.h").write_text("header", encoding="utf-8")

    assert sdks_materialize((spec,), ("sample",)) == (("sample", 1),)
    with pytest.raises(ConfigurationError, match="Unknown local SDKs"):
        sdks_materialize((spec,), ("missing",))
