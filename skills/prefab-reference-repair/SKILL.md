---
name: prefab-reference-repair
description: Broken reference repair workflow using reference-resolver service and where-used inspection, with ignore-guid policy and decision_required gating. Use when validate refs reports missing GUIDs or fileID errors.
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
