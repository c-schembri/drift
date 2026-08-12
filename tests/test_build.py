from pathlib import Path

from driftbuild.build import (
    BuildPhaseTiming,
    BuildTiming,
    _ninja_log_entries,
    _ninja_log_snapshot,
    _NinjaLogEntry,
    _phase_timings,
    build_timing_render,
)


def test_ninja_log_entries_only_read_appended_records(tmp_path: Path) -> None:
    log = tmp_path / ".ninja_log"
    log.write_text("# ninja log v5\n1\t11\t1\told.obj\tabc\n", encoding="utf-8")
    snapshot = _ninja_log_snapshot(log)
    with log.open("a", encoding="utf-8") as stream:
        stream.write("2\t22\t2\tnew.obj\tdef\n")

    assert _ninja_log_entries(log, snapshot) == (_NinjaLogEntry(2, 22, "new.obj", "def"),)


def test_phase_timings_accumulate_jobs_and_deduplicate_multi_output_edges(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "app.exe"
    import_library = tmp_path / "lib" / "app.lib"
    obj = tmp_path / "obj" / "app.obj"
    entries = (
        _NinjaLogEntry(0, 100, str(obj), "compile-hash"),
        _NinjaLogEntry(100, 140, str(executable), "link-hash"),
        _NinjaLogEntry(100, 140, str(import_library), "link-hash"),
    )
    phases = {
        str(obj.resolve()).replace("\\", "/").casefold(): "compile",
        str(executable.resolve()).replace("\\", "/").casefold(): "link",
        str(import_library.resolve()).replace("\\", "/").casefold(): "link",
    }

    assert _phase_timings(entries, phases) == (
        BuildPhaseTiming("compile", 0.1, 1),
        BuildPhaseTiming("link", 0.04, 1),
    )


def test_build_timing_render_includes_wall_and_job_times() -> None:
    timing = BuildTiming(0.5, 0.02, 0.45, (BuildPhaseTiming("compile", 0.8, 4),))

    assert build_timing_render(timing) == (
        "Build timing: total 0.500s | configure 0.020s | ninja 0.450s | compile 0.800s (4 jobs)"
    )
