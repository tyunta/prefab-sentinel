# TODO

- [x] Add a reusable parallel test runner entrypoint for local and CI use.
- [x] Verify the current suite passes under `unittest-parallel` and measure the command shape we should document.
- [x] Update CI and README to use the new parallel test command.
- [x] Run targeted review and refactor on the new test runner path.
- [x] Record verification results in this file.

## Verification

- `uv run --extra test python scripts/run_unit_tests.py` -> passed, 213 tests in parallel.
- `python3 scripts/run_unit_tests.py -k nonexistent` -> exits 2 with install guidance when `unittest_parallel` is missing.

## Authoring Sprint

- [x] Sync prefab create-mode hierarchy behavior and docs.
- [x] Add component lifecycle ops for prefab create mode.
- [x] Port create-mode mutation ops to component `$handle` targets.
- [x] Verify the authoring path with parallel unit tests and compile checks.

## Authoring Verification

- `python3 -m compileall unitytool/mcp/serialized_object.py tools/unity_patch_bridge.py tests/test_mcp_readonly.py tests/test_unity_patch_bridge.py tests/test_cli.py` -> passed.
- `uv run --extra test python scripts/run_unit_tests.py` -> passed, 222 tests in parallel.
