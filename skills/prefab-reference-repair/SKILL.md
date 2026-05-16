---
name: prefab-reference-repair
description: >-
  壊れた GUID / fileID 参照を検出し、一意な置換先のみ `safe_fix`、それ以外は
  `decision_required` で保留する。ignore-guid file (`<scope>/config/ignore_guids.txt`)
  と allowlist branch 経由で noise を整理する。
  トリガー: `validate_refs` で missing GUID / fileID が出た、Broken PPtr の調査。
---

# Prefab Reference Repair

## インターフェース
MCP ツールを直接呼び出す（CLI は廃止済み）。

## Overview
Detect and repair broken references while avoiding unsafe auto-fixes.

## Workflow
1. `validate_refs` MCP ツールで missing assets / fileIDs を特定する。
2. `find_referencing_assets` で壊れた参照の使用箇所を調査する。
3. 一意で決定的な置換先が存在すれば `safe_fix` を提案する。
4. 複数候補がある場合は `decision_required` として保留する。
5. ノイズの多い missing GUID は `validate_refs` の `ignore_asset_guids` パラメータで除外し、`<scope>/config/ignore_guids.txt` を更新する。
6. `validate_refs` を再実行してスコープがクリーンになったことを確認する。

## MCP ツール
- `validate_refs` — 壊れた GUID/fileID 参照のスキャン（`scope`, `ignore_asset_guids` パラメータ）
- `find_referencing_assets` — GUID/パスの参照元アセット検索（`asset_or_guid`, `scope` パラメータ）

## Guardrails
- Do not edit YAML directly.
- Only apply `safe_fix` when the candidate is unique and unambiguous.
- Ignore-guid updates in CI are restricted to allowlisted branches.

## 次にこれ

- 置換先が決まったら [`/prefab-sentinel:variant-safe-edit`](../variant-safe-edit/SKILL.md) で `set_property` / `patch_apply` を dry-run → confirm の経路に乗せる。
- 修復後にランタイム例外が再発したら [`/prefab-sentinel:udon-log-triage`](../udon-log-triage/SKILL.md) で再分類する。
- `validate_refs` / `find_referencing_assets` の引数や挙動を確認したいときは [`/prefab-sentinel:guide`](../guide/SKILL.md) と [docs/tools.md](../../docs/tools.md) を参照する。
