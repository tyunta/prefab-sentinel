---
name: udon-log-triage
description: >-
  Udon / ClientSim のランタイム例外を分類してアセット / コンポーネントに紐づけ、
  決定的な修正は `safe_fix`、判断が必要なものは `decision_required` で仕分ける。
  トリガー: ランタイム例外、Udon nullref、ClientSim 起動失敗、ログベースの regression。
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
export UNITYTOOL_BRIDGE_WATCH_DIR=/path/to/EditorBridge
```

## Guardrails
- If the Editor Bridge is unavailable (no `UNITYTOOL_BRIDGE_WATCH_DIR` or the watch directory is unreachable), mark the task as pending and stop after classification steps.
- Do not apply changes without audit logs (confirm mode with change_reason).
- WSL environments: project_root and asset_path are auto-converted to Windows format for Unity; watch_dir is auto-converted to WSL format for Python I/O.

## 次にこれ

- 例外が broken reference に由来する（`NullReferenceException` の対象 PPtr が解決できない）と判明したら [`/prefab-sentinel:prefab-reference-repair`](../prefab-reference-repair/SKILL.md) に切り替える。
- 修正方針が確定したら [`/prefab-sentinel:variant-safe-edit`](../variant-safe-edit/SKILL.md) で dry-run → confirm 経路に乗せる。
- ツールの選び方や Editor Bridge セットアップは [`/prefab-sentinel:guide`](../guide/SKILL.md) を参照する。
