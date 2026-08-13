from pathlib import Path

from driftbuild.cargo import _artifact_find, _depfile_write, _metadata_inputs


def test_artifact_find_discovers_binary_and_static_library() -> None:
    messages = [
        {
            "reason": "compiler-artifact",
            "target": {"name": "sample-server", "kind": ["bin"]},
            "executable": "target/debug/sample-server",
            "filenames": [],
        },
        {
            "reason": "compiler-artifact",
            "target": {"name": "sample_ffi", "kind": ["staticlib"]},
            "filenames": ["target/debug/sample_ffi.d", "target/debug/libsample_ffi.a"],
        },
    ]

    assert _artifact_find(messages, "bin", "sample-server") == Path("target/debug/sample-server")
    assert _artifact_find(messages, "staticlib", "sample-ffi") == Path("target/debug/libsample_ffi.a")


def test_artifact_find_prefers_static_library_over_cdylib_import_library() -> None:
    messages = [
        {
            "reason": "compiler-artifact",
            "target": {"name": "sample_ffi", "kind": ["cdylib", "staticlib"]},
            "filenames": [
                "target/debug/sample_ffi.dll",
                "target/debug/sample_ffi.dll.lib",
                "target/debug/sample_ffi.lib",
            ],
        }
    ]

    assert _artifact_find(messages, "staticlib", "sample-ffi") == Path("target/debug/sample_ffi.lib")


def test_metadata_inputs_discovers_all_local_package_sources(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    package = workspace / "crates/sample"
    (package / "src/nested").mkdir(parents=True)
    (workspace / ".cargo").mkdir(parents=True)
    manifest = package / "Cargo.toml"
    manifest.write_text("[package]\nname='sample'\nversion='0.1.0'\n", encoding="utf-8")
    source = package / "src/nested/module.rs"
    source.write_text("pub fn sample() {}\n", encoding="utf-8")
    ignored = package / "target/generated.rs"
    ignored.parent.mkdir()
    ignored.write_text("ignored", encoding="utf-8")
    lock = workspace / "Cargo.lock"
    lock.write_text("", encoding="utf-8")
    config = workspace / ".cargo/config.toml"
    config.write_text("", encoding="utf-8")

    inputs = _metadata_inputs(
        {
            "workspace_root": str(workspace),
            "packages": [
                {"manifest_path": str(manifest), "source": None},
                {"manifest_path": "registry/Cargo.toml", "source": "registry+https://example.invalid"},
            ],
        }
    )

    assert manifest.resolve() in inputs
    assert source.resolve() in inputs
    assert lock.resolve() in inputs
    assert config.resolve() in inputs
    assert ignored.resolve() not in inputs


def test_depfile_write_escapes_windows_style_paths(tmp_path: Path) -> None:
    depfile = tmp_path / "deps/sample.d"
    target = tmp_path / "output file.lib"
    source = tmp_path / "source file.rs"

    _depfile_write(depfile, target, (source,))

    text = depfile.read_text(encoding="utf-8")
    assert "output\\ file.lib" in text
    assert "source\\ file.rs" in text
