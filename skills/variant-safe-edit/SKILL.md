---
name: variant-safe-edit
description: >-
  Prefab / Scene / Asset を壊さず編集する。preflight 診断 → dry-run 差分 →
  confirm + change_reason 監査 → runtime 検証の 4 段ゲートを 1 経路で踏む。
  トリガー: パッチ適用、Prefab Variant 編集、`set_property` / `patch_apply` /
  `revert_overrides` を伴う変更全般。
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

## Editor Bridge セットアップ
Unity Editor 起動中の Editor Bridge を介してパッチ適用・ランタイム検証を実行する（エディタを閉じる必要はない）:
```bash
export UNITYTOOL_BRIDGE_WATCH_DIR=/path/to/EditorBridge
export UNITYTOOL_UNITY_TIMEOUT_SEC=30
```

適用後の目視確認:
- `editor_select` → `editor_frame` で対象を表示
- `editor_screenshot` で視覚確認

## Guardrails
- Do not edit YAML directly.
- Stop on `error` or `critical`; do not auto-apply `decision_required`.
- Unity targets require the resident Editor Bridge configured via `UNITYTOOL_BRIDGE_WATCH_DIR`; if unavailable, stop after dry-run.
- WSL environments: target paths are auto-converted to Windows format for Unity; watch_dir is auto-converted to WSL format for Python I/O.

## 次にこれ

- `validate_refs` で broken GUID / fileID が出たら [`/prefab-sentinel:prefab-reference-repair`](../prefab-reference-repair/SKILL.md) に切り替える（候補が複数あれば `decision_required` で保留）。
- `validate_runtime` で Udon / ClientSim 例外が出たら [`/prefab-sentinel:udon-log-triage`](../udon-log-triage/SKILL.md) で分類とアセットマッピングに移る。
- MCP ツールの仕様を確認したい / 別ツールを探したいときは [`/prefab-sentinel:guide`](../guide/SKILL.md) と [docs/tools.md](../../docs/tools.md) を参照する。
