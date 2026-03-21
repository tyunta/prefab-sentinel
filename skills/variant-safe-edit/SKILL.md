---
name: variant-safe-edit
description: Safe prefab variant editing workflow using Prefab Sentinel MCP/CLI with preflight scans, dry-run diffs, confirm-gated apply, and runtime validation. Use when editing Prefab/Scene/Assets or applying patch plans while avoiding broken references and enforcing audit logs.
---

# Variant Safe Edit

## 呼び出し方
```bash
uvx --from "${CLAUDE_PLUGIN_ROOT}" prefab-sentinel <command>
```
以下のコマンド例では `prefab-sentinel` を上記で読み替える。

## Overview
Provide a deterministic, fail-fast workflow for variant edits with auditability and reference safety.

## Workflow
1. Declare scope and target path, and record the change reason.
2. Preflight: run `list_overrides` and `scan_broken_references`.
3. Prepare a patch plan and run `dry_run_patch` to review diffs.
4. Stop on `error` or `critical`. Classify fixes as `safe_fix` or `decision_required`.
5. Apply only with `--confirm`, `--out-report`, and `--change-reason`.
6. If Unity runtime validation is available, run `compile_udonsharp`, `run_clientsim`, and log classification.

## Commands
```bash
# Preflight
prefab-sentinel inspect variant --path "Assets/... Variant.prefab"
prefab-sentinel validate refs --scope "Assets/YourScope"

# Generate a patch plan (e.g. circle layout)
prefab-sentinel patch generate circle --output Assets/Circle.prefab \
  --count 12 --radius 3.0 --out circle_plan.json

# Dry-run diff
prefab-sentinel patch apply --plan "config/patch_plan.json" --dry-run

# Apply with audit log
prefab-sentinel patch apply --plan "config/patch_plan.json" \
  --confirm \
  --out-report "reports/patch_result.json" \
  --change-reason "describe why this change is required"
```

## Editor Bridge Mode
When Unity Editor is already running, use Editor Bridge instead of batchmode to apply patches without closing the editor.

```bash
# Environment setup (WSL paths are auto-converted)
export UNITYTOOL_BRIDGE_MODE=editor
export UNITYTOOL_BRIDGE_WATCH_DIR=/mnt/d/VRC/World/EditorBridge
export UNITYTOOL_UNITY_TIMEOUT_SEC=30

# In Unity Editor: PrefabSentinel > Editor Bridge > set watch dir and enable
# Then apply as usual — bridge dispatches via file watcher
prefab-sentinel patch apply --plan circle_plan.json \
  --confirm --out-report reports/result.json \
  --change-reason "circle layout generation"

# Apply 後に Scene ビューで目視確認
prefab-sentinel editor select --path "/Canvas/MicPanel"
prefab-sentinel editor frame
prefab-sentinel editor screenshot --view scene
```

## Guardrails
- Do not edit YAML directly.
- Stop on `error` or `critical`; do not auto-apply `decision_required`.
- Unity targets require a bridge (batchmode via `UNITYTOOL_UNITY_COMMAND` or Editor Bridge via `UNITYTOOL_BRIDGE_MODE=editor`); if unavailable, stop after dry-run.
- WSL environments: target paths are auto-converted to Windows format for Unity; watch_dir is auto-converted to WSL format for Python I/O.
