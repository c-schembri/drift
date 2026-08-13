from pathlib import Path

from driftbuild.cargo import _artifact_find


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
