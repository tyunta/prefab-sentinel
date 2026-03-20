---
name: udon-log-triage
description: Udon/ClientSim log triage workflow using runtime-validation MCP to classify errors, map to assets, and propose fixes as safe_fix or decision_required. Use when runtime exceptions or log-based regressions occur.
---

# Udon Log Triage

## 呼び出し方
```bash
uvx --from "${CLAUDE_PLUGIN_ROOT}" prefab-sentinel <command>
```
以下のコマンド例では `prefab-sentinel` を上記で読み替える。

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
# Batchmode (default)
prefab-sentinel validate runtime --scene "Assets/Scenes/Smoke.unity"

# Editor Bridge mode (Unity Editor running)
export UNITYTOOL_BRIDGE_MODE=editor
export UNITYTOOL_BRIDGE_WATCH_DIR=/mnt/d/VRC/World/EditorBridge
prefab-sentinel validate runtime --scene "Assets/Scenes/Smoke.unity"

prefab-sentinel inspect where-used --asset-or-guid "Assets/SomeAsset.prefab" --scope "Assets"
```

## Guardrails
- If Unity runtime is unavailable (no batchmode command and no Editor Bridge), mark the task as pending and stop after classification steps.
- Do not apply changes without audit logs (`--confirm --out-report --change-reason`).
- WSL environments: project_root and scene_path are auto-converted to Windows format for Unity; watch_dir is auto-converted to WSL format for Python I/O.
