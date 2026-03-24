---
name: udon-log-triage
description: Udon/ClientSim log triage workflow using runtime-validation service to classify errors, map to assets, and propose fixes as safe_fix or decision_required. Use when runtime exceptions or log-based regressions occur.
---

# Udon Log Triage

## インターフェース
MCP ツールを直接呼び出す（CLI は廃止済み）。

## Overview
Reduce runtime failures by classifying logs, mapping errors to assets, and controlling fixes.

## Workflow
1. `validate_runtime` MCP ツールでランタイムログを収集・分類する。
2. エラーの分類結果から `assert_no_critical_errors` のステップを確認する。
3. `find_referencing_assets` でエラー箇所をアセット/コンポーネントにマッピングする。
4. 修正を提案: 決定的で一意な候補のみ `safe_fix`、それ以外は `decision_required`。
5. ランタイム検証を再実行してレポートを保存する。

## MCP ツール
- `validate_runtime` — UdonSharp コンパイル + ClientSim 実行検証（`asset_path` パラメータ）
- `find_referencing_assets` — エラー箇所のアセット参照検索
- `inspect_wiring` — MonoBehaviour フィールド配線検査

## Editor Bridge モード
Unity Editor 起動中は以下の環境変数で Editor Bridge 経由の検証が可能:
```bash
export UNITYTOOL_BRIDGE_MODE=editor
export UNITYTOOL_BRIDGE_WATCH_DIR=/mnt/d/VRC/World/EditorBridge
```

## Guardrails
- If Unity runtime is unavailable (no batchmode command and no Editor Bridge), mark the task as pending and stop after classification steps.
- Do not apply changes without audit logs (confirm mode with change_reason).
- WSL environments: project_root and asset_path are auto-converted to Windows format for Unity; watch_dir is auto-converted to WSL format for Python I/O.
