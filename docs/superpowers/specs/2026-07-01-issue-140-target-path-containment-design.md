# Issue #140 target_path containment design

## Sources

- [Issue #140: orchestrator_variant target_path can escape project_root](https://github.com/tyunta/prefab-sentinel-dev/issues/140)
- [Python pathlib documentation](https://docs.python.org/3/library/pathlib.html)

## Context

Issue #140 reports a pre-existing security problem in
`orchestrator_variant.read_target_file`: inspect tools that accept
`target_path` can resolve traversal or absolute paths outside
`project_root` before reading Unity YAML text.

The affected public paths called out by the issue are `inspect_structure`
and `inspect_wiring`. The underlying trust boundary is the shared
`read_target_file` helper, which is also used by other read-only inspect
tools.

## Goals

- Require `target_path` inputs read through `read_target_file` to be
  project-root-relative.
- Reject absolute paths, `..` traversal segments, and paths whose resolved
  filesystem location is outside `project_root`.
- Return the existing structured `ToolResponse` error envelope on rejection.
- Add regression coverage for both `inspect_structure` and `inspect_wiring`
  with absolute-path and traversal inputs.
- Keep the change scoped to the target-file read boundary.

## Non-goals

- Do not change the global `resolve_scope_path` contract. Existing callers
  and tests rely on it accepting absolute paths.
- Do not add broad lexical validation for empty paths, `.`, trailing slashes,
  or file extensions as part of this issue.
- Do not introduce a reusable path-policy framework until a second concrete
  use case exists.
- Do not require Unity Editor validation. This is Python read-only path
  validation, not serialized asset editing or Editor Bridge behavior.

## Architecture

Add a private target-path validation helper inside
`prefab_sentinel/orchestrator_variant.py` and call it at the start of
`read_target_file`.

`resolve_scope_path` remains the generic resolver. The stricter policy belongs
to `read_target_file` because this is where untrusted inspect-tool input
crosses into filesystem reads.

The helper should return either a safe resolved `Path` or a `ToolResponse`
error. This keeps `read_target_file` as the single public-ish entry point for
target file reads and avoids pushing security policy into each caller.

## Data Flow

For `read_target_file(prefab_variant, target_path, code_prefix)`:

1. Convert `target_path` through the existing WSL-compatible path conversion
   used by path resolution.
2. Construct a `Path` from the converted target.
3. Reject the path if it is absolute.
4. Reject the path if any path part is `..`.
5. Resolve `prefab_variant.project_root`.
6. Resolve `project_root / target_path`.
7. Reject the path if the resolved target is not under the resolved project
   root.
8. Continue with the existing `exists()` and `decode_text_file()` behavior.

This preserves normal inputs such as `Assets/Base.prefab` while rejecting
inputs such as `/tmp/outside.prefab`, `C:\tmp\outside.prefab`,
`../outside.prefab`, `Assets/../outside.prefab`, and symlink escapes that
resolve outside the project root.

## Error Handling

Use `error_response(...)` and derive the code from the existing `code_prefix`:

- `VALIDATE_STRUCTURE_INVALID_TARGET_PATH`
- `INSPECT_WIRING_INVALID_TARGET_PATH`
- Other callers of the shared helper receive the equivalent
  `{PREFIX}_INVALID_TARGET_PATH`.

The response must use `severity="error"` through the existing helper behavior.
The `data` payload should match existing target-file read errors:

- `target_path`: original caller input
- `read_only`: `true`

The message should state that `target_path` must be project-root-relative and
stay within `project_root`. Absolute, traversal, and resolved-outside failures
share the same code and message shape.

## Testing

Add regression tests at the public inspect-tool level:

- `inspect_structure` with an absolute path returns
  `VALIDATE_STRUCTURE_INVALID_TARGET_PATH`.
- `inspect_structure` with a `..` traversal path returns
  `VALIDATE_STRUCTURE_INVALID_TARGET_PATH`.
- `inspect_wiring` with an absolute path returns
  `INSPECT_WIRING_INVALID_TARGET_PATH`.
- `inspect_wiring` with a `..` traversal path returns
  `INSPECT_WIRING_INVALID_TARGET_PATH`.

Each test should pin:

- `success == False`
- `severity == "error"`
- the exact error `code`
- `data["target_path"]`
- `data["read_only"] is True`
- a message fragment indicating the project-root-relative containment rule

Symlink escape is covered by the resolved containment check in implementation.
It is optional as a dedicated regression test unless it fits naturally without
OS-specific noise.

Run at least:

```sh
uv run pytest tests/test_orchestrator_validation.py tests/test_orchestrator_wiring_filter.py
```

Broaden to related tests if this targeted run exposes coupling.

## Issue Workflow

After implementation and verification:

1. Merge the fix to `main` or include `Fixes #140` in the PR path.
2. Comment on Issue #140 with the implementation summary and pytest command.
3. Close Issue #140 if the fix is on `main` and the targeted regression tests
   pass.
