# MCP パラメータ命名統一 — 設計仕様

**日付**: 2026-03-25
**バージョン**: v0.5.0 (Breaking Change)
**スコープ**: MCP ツール + Editor Bridge ワイヤプロトコルのパラメータ名統一

---

## 背景

v0.4.0 で CLI を削除し MCP が唯一のインターフェースとなった。MCP 統合テスト (`report_mcp_integration_test_20260324.md`) で、パラメータ名の不整合により AI エージェントが何度もバリデーションエラーを起こす問題が顕在化した。

主な不整合:
- 同じ意味のパラメータに異なる名前 (`path` / `scene_path` / `prefab_path` / `variant_path`)
- GUID 兼用パラメータの命名パターンが不統一 (`asset_or_guid` / `script_path_or_guid`)
- 深さパラメータが 3 種 (`depth` / `max_depth` / `list_depth`)

## 設計方針

- **Breaking Change 許容**: エイリアスや deprecation は入れない。
- **MCP + Editor Bridge**: MCP 関数シグネチャと C# ワイヤプロトコルを揃える。orchestrator / services 層の内部変数名は変更しない。
- **純粋リネーム**: ツールの追加・削除・機能変更はしない。

## 命名規則

| カテゴリ | 規則 | 説明 |
|----------|------|------|
| アセットファイルパス | `asset_path` | `.prefab`, `.unity`, `.mat`, `.asset` へのパス |
| ランタイム階層パス | `hierarchy_path` | Unity Editor のシーン階層パス |
| シンボルパス | `symbol_path` | prefab-sentinel のシンボルアドレッシング (変更なし) |
| GUID のみ受付 | `*_guid` | `material_guid` 等 |
| パスまたは GUID 受付 | `*_or_guid` | `asset_or_guid`, `script_or_guid` 等 |
| 深さ | `depth` | 全ツール共通 |
| スコープ | `scope` | 変更なし |
| 確認ゲート | `confirm` + `change_reason` | 変更なし |

## 変更一覧

### パラメータリネーム (21 件)

| # | ツール | 旧パラメータ | 新パラメータ |
|---|--------|------------|------------|
| 1 | `get_unity_symbols` | `path` | `asset_path` |
| 2 | `find_unity_symbol` | `path` | `asset_path` |
| 3 | `inspect_wiring` | `path` | `asset_path` |
| 4 | `inspect_variant` | `path` | `asset_path` |
| 5 | `diff_unity_symbols` | `path` | `asset_path` |
| 6 | `set_property` | `path` | `asset_path` |
| 7 | `add_component` | `path` | `asset_path` |
| 8 | `remove_component` | `path` | `asset_path` |
| 9 | `inspect_materials` | `path` | `asset_path` |
| 10 | `validate_structure` | `path` | `asset_path` |
| 11 | `inspect_hierarchy` | `path` | `asset_path` |
| 12 | `validate_runtime` | `scene_path` | `asset_path` |
| 13 | `revert_overrides` | `variant_path` | `asset_path` |
| 14 | `editor_instantiate` | `prefab_path` | `asset_path` |
| 15 | `editor_get_material_property` | `renderer_path` | `hierarchy_path` |
| 16 | `editor_set_material` | `renderer_path` | `hierarchy_path` |
| 17 | `editor_instantiate` | `parent_path` | `hierarchy_path` |
| 18 | `list_serialized_fields` | `script_path_or_guid` | `script_or_guid` |
| 19 | `validate_field_rename` | `script_path_or_guid` | `script_or_guid` |
| 20 | `inspect_hierarchy` | `max_depth` | `depth` |
| 21 | `editor_list_children` | `list_depth` | `depth` |

### 変更しないもの

- `scope` — 名前は統一済み。optionality の差はセマンティクスに基づく。
- `confirm` / `change_reason` — 統一済み。
- `symbol_path` — 統一済み。
- `hierarchy_path` (`editor_select`, `editor_list_children`, `editor_delete`) — 既にこの名前。
- `patch_apply` の `runtime_*` プレフィックス — `validate_runtime` と名前空間が異なるため妥当。
- `material_guid` (`editor_set_material`) — GUID のみ受付なので `*_guid` 規則に合致。

orchestrator / services 層の内部変数名は変更しない。MCP 関数内で `asset_path` を受け取り、orchestrator 呼び出し時に旧パラメータ名へ変換して渡す:

```python
# validate_runtime: asset_path を受け取る
orch.validate_runtime(scene_path=asset_path, ...)

# revert_overrides: asset_path を受け取る
revert_overrides_impl(variant_path=asset_path, ...)

# inspect_hierarchy: depth を受け取る
orch.inspect_hierarchy(path=asset_path, max_depth=depth, ...)
```

## Editor Bridge ワイヤプロトコル — エンドツーエンド統一

Editor Bridge 系ツール (items 14-17, 21) は `send_action(**kwargs)` 経由で C# `EditorControlRequest` に JSON フィールドを渡す。MCP・ワイヤ JSON・C# を全て同じ名前に揃える。リマップ層は不要になる。

### C# 側の変更 (EditorControlRequest)

| 旧フィールド名 | 新フィールド名 | 対象ツール |
|---------------|---------------|-----------|
| `renderer_path` | `hierarchy_path` | `editor_get_material_property`, `editor_set_material` |
| `parent_path` | `hierarchy_path` | `editor_instantiate` |
| `prefab_path` | `asset_path` | `editor_instantiate` |
| `list_depth` | `depth` | `editor_list_children` |

変更対象ファイル:
- `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` — `EditorControlRequest` 構造体 + Handler メソッド
- `tools/unity/PrefabSentinel.UnityIntegrationTests.cs` — テスト内の JSON フィールド参照

### 複数リネームが発生するツール

| ツール | リネーム数 | 旧 → 新 |
|--------|-----------|----------|
| `editor_instantiate` | 2 | `prefab_path` → `asset_path`, `parent_path` → `hierarchy_path` |
| `inspect_hierarchy` | 2 | `path` → `asset_path`, `max_depth` → `depth` |

## 影響範囲 (更新)

| ファイル | 変更内容 |
|----------|----------|
| `prefab_sentinel/mcp_server.py` | 21 パラメータのリネーム (関数シグネチャ + 内部参照) |
| `tests/test_mcp_server.py` | テストの引数名を追従 |
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | `EditorControlRequest` フィールド名 + Handler 内参照 |
| `tools/unity/PrefabSentinel.UnityIntegrationTests.cs` | テスト内の JSON フィールド参照 |
| `skills/guide/SKILL.md` | パラメータ名の記述更新 |
| `skills/variant-safe-edit/SKILL.md` | 該当箇所のパラメータ名更新 |
| `skills/prefab-reference-repair/SKILL.md` | 該当箇所のパラメータ名更新 |
| `skills/udon-log-triage/SKILL.md` | 該当箇所のパラメータ名更新 |
| `README.md` | MCP ツール説明のパラメータ名更新 |
| `pyproject.toml` | minor バンプ → v0.5.0 |

## やらないこと

- orchestrator / services 層のリネーム。
- エイリアスや deprecation warning。
- ツールの追加・削除・機能変更。
- ドキュメント以外の振る舞い変更。

## 検証基準

1. 全ユニットテスト pass (1230 件)
2. MCP ツール登録数 41 確認
3. `mcp_server.py`, `test_mcp_server.py`, `skills/`, `README.md` 内の旧パラメータ名 grep → 0 件 (orchestrator / 歴史的 spec は対象外)
4. C# コンパイルチェック pass (`python3 -m py_compile` 相当は不要だが、旧フィールド名の grep → 0 件)
5. 手動 MCP スモーク: `activate_project` → `validate_refs` → `inspect_hierarchy` の 3 ツール実行成功
