# Drift Agent Rules

- Read `docs/architecture.md` before changing public APIs or subsystem boundaries.
- Run `git status --short` before editing and leave unrelated work untouched.
- Keep `driftbuild.api` as the supported provider surface. Provider code must not import backend internals.
- Project evaluation declares immutable data. It must not compile, copy, publish, or contact remote services.
- Runtime code uses only the Python 3.12 standard library. Development dependencies belong in `dependency-groups`.
- Add focused tests for every public behavior and backend contract change.
- Prefer explicit inputs and outputs. Reject invalid graphs before invoking Ninja.
- Use `ruff check .`, `mypy driftbuild`, and `pytest` before handoff.
