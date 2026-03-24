---
name: variant-safe-edit
description: Safe prefab variant editing workflow using Prefab Sentinel MCP tools with preflight scans, dry-run diffs, confirm-gated apply, and runtime validation. Use when editing Prefab/Scene/Assets or applying patch plans while avoiding broken references and enforcing audit logs.
---

# Variant Safe Edit

## インターフェース
MCP ツールを直接呼び出す（CLI は廃止済み）。

## Overview
Provide a deterministic, fail-fast workflow for variant edits with auditability and reference safety.

## Workflow
1. スコープとターゲットパスを宣言し、変更理由を記録する。
2. Preflight: `inspect_variant` と `validate_refs` で事前診断する。
3. パッチ計画を準備し、`patch_apply` の dry-run モードで差分を確認する。
4. `error` / `critical` で停止。修正を `safe_fix` または `decision_required` に分類する。
5. confirm モードで適用（`change_reason` 必須）。
6. Unity ランタイム検証が利用可能なら `validate_runtime` を実行する。

## MCP ツール
- `inspect_variant` — Prefab Variant のオーバーライドチェーン分析
- `validate_refs` — 壊れた参照のスキャン
- `patch_apply` — パッチ計画の検証・適用（`plan_json` で JSON 文字列入力、`dry_run`/`confirm` モード）
- `validate_runtime` — UdonSharp コンパイル + ClientSim 検証
- `revert_overrides` — Variant の特定オーバーライドを削除
- `set_property` — シンボルパスでフィールド値を設定

## Editor Bridge モード
Unity Editor 起動中は Editor Bridge 経由でパッチ適用が可能（エディタを閉じずに実行）:
```bash
export UNITYTOOL_BRIDGE_MODE=editor
export UNITYTOOL_BRIDGE_WATCH_DIR=/mnt/d/VRC/World/EditorBridge
export UNITYTOOL_UNITY_TIMEOUT_SEC=30
```

適用後の目視確認:
- `editor_select` → `editor_frame` で対象を表示
- `editor_screenshot` で視覚確認

## Guardrails
- Do not edit YAML directly.
- Stop on `error` or `critical`; do not auto-apply `decision_required`.
- Unity targets require a bridge (batchmode via `UNITYTOOL_UNITY_COMMAND` or Editor Bridge via `UNITYTOOL_BRIDGE_MODE=editor`); if unavailable, stop after dry-run.
- WSL environments: target paths are auto-converted to Windows format for Unity; watch_dir is auto-converted to WSL format for Python I/O.
