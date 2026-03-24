# CLI Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove CLI (`prefab-sentinel` command) and all CLI-only code, leaving MCP as the sole interface.

**Architecture:** Delete cli.py + __main__.py + bridge_check.py and their tests. Remove CLI-dependent test classes from 4 other test files. Remove suggest_ignore_guids from orchestrator. Update CI workflows to call Python modules directly. Update pyproject.toml and README.

**Tech Stack:** Python 3.11, unittest, GitHub Actions YAML

**Spec:** `docs/superpowers/specs/2026-03-24-cli-deletion-design.md`

---

### Task 1: Delete core CLI files

**Files:**
- Delete: `prefab_sentinel/cli.py`
- Delete: `prefab_sentinel/__main__.py`
- Delete: `tests/test_cli.py`
- Delete: `prefab_sentinel/bridge_check.py`
- Delete: `tests/test_bridge_check.py`

- [ ] **Step 1: Delete the 5 files**

```bash
git rm prefab_sentinel/cli.py prefab_sentinel/__main__.py tests/test_cli.py prefab_sentinel/bridge_check.py tests/test_bridge_check.py
```

- [ ] **Step 2: Verify import works**

Run: `uv run python -c "import prefab_sentinel; print('OK')"`
Expected: `OK` (no ImportError)

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor: delete CLI core files (cli.py, __main__.py, bridge_check.py) and tests"
```

---

### Task 2: Remove CLI-dependent test classes

**Files:**
- Modify: `tests/test_editor_bridge.py` — delete `TestCliEditorSubcommands` class (lines 189-429)
- Modify: `tests/test_mcp_server.py` — delete `TestCLIServeCommand` class (lines 909-931)
- Modify: `tests/test_patch_revert.py` — delete `PatchRevertCliTests` class (lines 301-405), remove `from prefab_sentinel import cli` (line 10), remove unused imports `io`, `json`, `redirect_stdout` if no longer used
- Modify: `tests/test_integration_tests.py` — delete `CliIntegrationTestsTests` class (lines 190-201)

- [ ] **Step 1: Remove TestCliEditorSubcommands from test_editor_bridge.py**

Delete lines 189-429 (the entire `TestCliEditorSubcommands` class). The file should end after `TestEditorBridgeSupportedActions` at line 187, then the `if __name__` block.

- [ ] **Step 2: Remove TestCLIServeCommand from test_mcp_server.py**

Delete lines 909-931 (the entire `TestCLIServeCommand` class).

- [ ] **Step 3: Remove PatchRevertCliTests and cli import from test_patch_revert.py**

Delete line 10: `from prefab_sentinel import cli`
Delete lines 3-4 and 7 (now-unused imports): `import io`, `import json`, `from contextlib import redirect_stdout`
Delete lines 301-405 (the entire `PatchRevertCliTests` class).

Keep: `import tempfile`, `from pathlib import Path`, `from prefab_sentinel.patch_revert import revert_overrides`, `from tests.bridge_test_helpers import write_file`, and all non-CLI test classes (`PatchRevertTests`, `PatchRevertDuplicateOverrideTests`).

- [ ] **Step 4: Remove CliIntegrationTestsTests from test_integration_tests.py**

Delete lines 190-201 (the entire `CliIntegrationTestsTests` class).

- [ ] **Step 5: Run ruff check on modified files**

Run: `uv run ruff check tests/test_editor_bridge.py tests/test_mcp_server.py tests/test_patch_revert.py tests/test_integration_tests.py`
Expected: No errors. Fix any unused import warnings before proceeding.

- [ ] **Step 6: Run modified test files**

Run: `uv run --extra test python -m unittest tests.test_editor_bridge tests.test_mcp_server tests.test_patch_revert tests.test_integration_tests -v 2>&1 | tail -5`
Expected: All tests pass, no import errors.

- [ ] **Step 7: Commit**

```bash
git add tests/test_editor_bridge.py tests/test_mcp_server.py tests/test_patch_revert.py tests/test_integration_tests.py
git commit -m "refactor: remove CLI-dependent test classes from 4 test files"
```

---

### Task 3: Remove suggest_ignore_guids from orchestrator and tests

**Files:**
- Modify: `prefab_sentinel/orchestrator.py` — delete `suggest_ignore_guids()` method (lines 1068-1212)
- Modify: `tests/test_orchestrator.py` — delete `SuggestIgnoreGuidsTests` class (line 730 onward, ends before `ValidateRuntimeTests` at line 828)
- Modify: `tests/test_services.py` — delete `test_suggest_ignore_guids_returns_candidates` (lines 2374-2396) and `test_suggest_ignore_guids_respects_ignore_list` (lines 2398-2412) from `OrchestratorSuggestionTests`

- [ ] **Step 1: Delete suggest_ignore_guids method from orchestrator.py**

Delete lines 1068-1212 (the entire `suggest_ignore_guids` method). The file should go from the method before it directly to `validate_runtime` at what was line 1214.

- [ ] **Step 2: Delete SuggestIgnoreGuidsTests from test_orchestrator.py**

Delete the entire `SuggestIgnoreGuidsTests` class (lines 730-826).

- [ ] **Step 3: Delete suggest_ignore_guids tests from test_services.py**

Delete `test_suggest_ignore_guids_returns_candidates` (lines 2374-2396) and `test_suggest_ignore_guids_respects_ignore_list` (lines 2398-2412) from `OrchestratorSuggestionTests`. Keep the class if it has other tests; delete it if empty.

Check: `OrchestratorSuggestionTests` at line 2373 — after removing both methods, check if `test_inspect_where_used_wraps_reference_result` (line 2414) is in this class or another. If the class has remaining tests, keep it.

- [ ] **Step 4: Run ruff check on modified files**

Run: `uv run ruff check prefab_sentinel/orchestrator.py tests/test_orchestrator.py tests/test_services.py`
Expected: No errors.

- [ ] **Step 5: Run affected tests**

Run: `uv run --extra test python -m unittest tests.test_orchestrator tests.test_services -v 2>&1 | tail -5`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/orchestrator.py tests/test_orchestrator.py tests/test_services.py
git commit -m "refactor: remove suggest_ignore_guids from orchestrator and tests"
```

---

### Task 4: Update pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Remove CLI entry point**

Delete line 20: `prefab-sentinel = "prefab_sentinel.cli:main"`

The `[project.scripts]` section should contain only:
```toml
[project.scripts]
prefab-sentinel-mcp = "prefab_sentinel.mcp_server:main"
```

- [ ] **Step 2: Update project description**

Change line 8 from:
```toml
description = "Prefab Sentinel CLI for safe Unity Prefab/Scene inspection and editing."
```
to:
```toml
description = "Prefab Sentinel — Unity Prefab/Scene inspection and editing toolkit."
```

- [ ] **Step 3: Remove cli from mypy overrides**

Delete `"prefab_sentinel.cli",` from the `[[tool.mypy.overrides]]` module list (line 47).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: remove CLI entry point, update description, clean mypy overrides"
```

---

### Task 5: Update CI workflows

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/unity-integration.yml`
- Modify: `.github/workflows/unity-smoke.yml`

- [ ] **Step 1: Update ci.yml smoke-batch step**

Change (lines 77-88):
```yaml
          uv run prefab-sentinel validate smoke-batch \
```
to:
```yaml
          uv run python -m prefab_sentinel.smoke_batch \
```

Arguments stay the same (they start at `--targets`, no CLI subcommand prefix).

- [ ] **Step 2: Update ci.yml smoke-history step**

Change (lines 92-99):
```yaml
          uv run prefab-sentinel report smoke-history \
```
to:
```yaml
          uv run python -m prefab_sentinel.smoke_history \
```

Arguments stay the same.

- [ ] **Step 3: Update ci.yml integration-test-contract step**

Replace only the command on line 129 (preserve the surrounding `set +e` and exit code checking logic on lines 128, 134-139):
```yaml
          uv run prefab-sentinel validate integration-tests \
```
to:
```yaml
          uv run python scripts/unity_integration_tests.py \
```

Arguments stay the same (`--unity-command`, `--unity-project-path`, `--out-dir`, `--skip-deploy`).

- [ ] **Step 4: Update unity-integration.yml**

Change (lines 50-55):
```yaml
          prefab-sentinel validate integration-tests
          --unity-command "${{ inputs.unity_command }}"
          --unity-project-path "${{ inputs.unity_project_path }}"
          --out-dir reports/integration
          --timeout-sec ${{ inputs.unity_timeout_sec }}
```
to:
```yaml
          python scripts/unity_integration_tests.py
          --unity-command "${{ inputs.unity_command }}"
          --unity-project-path "${{ inputs.unity_project_path }}"
          --out-dir reports/integration
          --timeout-sec ${{ inputs.unity_timeout_sec }}
```

Note: This runner uses `pip install -e .`, not `uv`. Use bare `python`.

- [ ] **Step 5: Update unity-smoke.yml smoke-batch call**

Change (lines 436-470):

Remove CLI subcommand prefix from `$args`:
```powershell
          $args = @(
            "--targets", "${{ inputs.targets }}",
            "--avatar-plan", "${{ inputs.avatar_plan }}",
            ...
          )
```
(Delete `"validate",` and `"smoke-batch",` from the array.)

Change the invocation:
```powershell
          python -m prefab_sentinel.smoke_batch @args
```

- [ ] **Step 6: Update unity-smoke.yml smoke-history call**

Change (lines 476-525):

Remove CLI subcommand prefix from `$args`:
```powershell
          $args = @(
            "--inputs", "reports/bridge_smoke/summary.json",
            ...
          )
```
(Delete `"report",` and `"smoke-history",` from the array.)

Change the invocation:
```powershell
          python -m prefab_sentinel.smoke_history @args
```

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/unity-integration.yml .github/workflows/unity-smoke.yml
git commit -m "ci: replace CLI commands with direct Python module invocations"
```

---

### Task 6: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Remove CLI usage sections**

Remove CLI command examples (sections showing `prefab-sentinel inspect ...`, `prefab-sentinel validate ...`, `prefab-sentinel patch ...`, etc.). Keep the MCP tool table as the primary interface documentation.

Update the project overview to reflect MCP-only architecture. Remove references to the `prefab-sentinel` command.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: remove CLI sections from README, MCP is now the sole interface"
```

---

### Task 7: Full verification and version bump

- [ ] **Step 1: Run full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: All tests pass (count will be significantly lower than 1264 due to deleted CLI tests).

- [ ] **Step 2: Verify MCP tools still registered**

Run: `uv run --extra test python -m unittest tests.test_mcp_server.TestToolRegistration -v`
Expected: 36 tools registered, both tests pass.

- [ ] **Step 3: Verify module import**

Run: `uv run python -c "import prefab_sentinel; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Verify smoke_batch and smoke_history CLI alternatives**

Run: `uv run python -m prefab_sentinel.smoke_batch --help`
Expected: Help text with `--targets` etc.

Run: `uv run python -m prefab_sentinel.smoke_history --help`
Expected: Help text with `--inputs` etc.

- [ ] **Step 5: Run ruff check**

Run: `uv run ruff check prefab_sentinel/ tests/`
Expected: No errors.

- [ ] **Step 6: Version bump**

Run: `uv run bump-my-version bump minor`
Expected: Version bumps to 0.4.0.

- [ ] **Step 7: Commit version bump**

```bash
git add pyproject.toml .claude-plugin/plugin.json
git commit -m "chore: bump version to 0.4.0 (CLI removal, breaking change)"
```
