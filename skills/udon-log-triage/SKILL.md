---
name: udon-log-triage
description: Udon/ClientSim log triage workflow using runtime-validation MCP to classify errors, map to assets, and propose fixes as safe_fix or decision_required. Use when runtime exceptions or log-based regressions occur.
---

# Udon Log Triage

## Overview
Reduce runtime failures by classifying logs, mapping errors to assets, and controlling fixes.

## Workflow
1. Collect runtime logs with `validate runtime` or `run_clientsim`.
2. Classify errors with `classify_errors` and assert with `assert_no_critical_errors`.
3. Map error locations to assets/components using `inspect where-used`.
4. Propose fixes: use `safe_fix` only for deterministic, unique candidates; otherwise `decision_required`.
5. Re-run runtime validation and save the report.

## Commands
```bash
prefab-sentinel validate runtime --scene "Assets/Scenes/Smoke.unity"
prefab-sentinel inspect where-used --asset-or-guid "Assets/SomeAsset.prefab" --scope "Assets"
```

## Guardrails
- If Unity runtime is unavailable, mark the task as pending and stop after classification steps.
- Do not apply changes without audit logs (`--confirm --out-report --change-reason`).
