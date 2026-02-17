---
name: variant-safe-edit
description: Safe prefab variant editing workflow using Prefab Sentinel MCP/CLI with preflight scans, dry-run diffs, confirm-gated apply, and runtime validation. Use when editing Prefab/Scene/Assets or applying patch plans while avoiding broken references and enforcing audit logs.
---

# Variant Safe Edit

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

# Dry-run diff
prefab-sentinel patch apply --plan "config/patch_plan.json" --dry-run

# Apply with audit log
prefab-sentinel patch apply --plan "config/patch_plan.json" \
  --confirm \
  --out-report "reports/patch_result.json" \
  --change-reason "describe why this change is required"
```

## Guardrails
- Do not edit YAML directly.
- Stop on `error` or `critical`; do not auto-apply `decision_required`.
- Unity targets require `UNITYTOOL_PATCH_BRIDGE`; if unavailable, stop after dry-run.
