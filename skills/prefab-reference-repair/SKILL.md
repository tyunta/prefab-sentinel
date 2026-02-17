---
name: prefab-reference-repair
description: Broken reference repair workflow using reference-resolver MCP and where-used inspection, with ignore-guid policy and decision_required gating. Use when validate refs reports missing GUIDs or fileID errors.
---

# Prefab Reference Repair

## Overview
Detect and repair broken references while avoiding unsafe auto-fixes.

## Workflow
1. Run `validate refs --scope ...` to identify missing assets or fileIDs.
2. Use `inspect where-used` to locate and understand each broken reference.
3. If a unique, deterministic replacement exists, propose a `safe_fix`.
4. If multiple candidates exist, mark as `decision_required` and stop.
5. For noisy missing GUIDs, use `suggest ignore-guids` and update `<scope>/config/ignore_guids.txt`.
6. Re-run `validate refs` to confirm the scope is clean.

## Commands
```bash
prefab-sentinel validate refs --scope "Assets/YourScope"
prefab-sentinel inspect where-used --asset-or-guid "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" --scope "Assets/YourScope"
prefab-sentinel suggest ignore-guids --scope "Assets/YourScope"
```

## Guardrails
- Do not edit YAML directly.
- Only apply `safe_fix` when the candidate is unique and unambiguous.
- Ignore-guid updates in CI are restricted to allowlisted branches.
