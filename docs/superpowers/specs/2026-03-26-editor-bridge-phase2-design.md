# Editor Bridge 拡張 Phase 2 設計

## Goal

Editor Bridge に BlendShape 操作とメニュー実行の 4 ツールを追加し、AI エージェントが Unity Editor のランタイム操作・メニュー発火をできるようにする。

## 背景

- report_20260325_blendshape_support.md で BlendShape 操作の既存手段が不十分と判明
- VRChat エコシステムツール (NDMF, VRCFury, AAO 等) の処理は Unity メニュー項目から発火するものが多く、MCP から呼べない

## スコープ

### In Scope

- `editor_get_blend_shapes`: BlendShape 一覧 + 現在値の取得
- `editor_set_blend_shape`: BlendShape ウェイトの設定（名前指定）
- `editor_list_menu_items`: `[MenuItem]` 属性のリフレクションスキャンによるメニュー項目一覧
- `editor_execute_menu_item`: `EditorApplication.ExecuteMenuItem()` によるメニュー項目実行

### Out of Scope

- Inspector ボタンの発火 (カスタム GUI コードのため汎用的な手段がない)
- Nested Prefab のシンボルツリー展開 (別課題)
- Scene open モードの `$scene` ハンドル修正 (別課題)
- `patch_apply` での BlendShape 名前指定シンタックスシュガー (`editor_set_blend_shape` で代替)

## 新設ツール API

### editor_get_blend_shapes

SkinnedMeshRenderer の BlendShape 一覧と現在のウェイト値を取得する。

```
editor_get_blend_shapes(
    hierarchy_path: str,       # SkinnedMeshRenderer を持つ GameObject
    filter: str = ""           # 名前部分一致フィルタ（空=全件）
)
```

レスポンス:
```json
{
    "success": true,
    "severity": "info",
    "code": "EDITOR_CTRL_BLEND_SHAPES_OK",
    "data": {
        "blend_shapes": [
            { "index": 0, "name": "vrc.v_aa", "weight": 0.0 },
            { "index": 1, "name": "vrc.v_oh", "weight": 0.0 }
        ],
        "count": 800,
        "renderer_path": "Body"
    }
}
```

実装: `SkinnedMeshRenderer.sharedMesh.GetBlendShapeName(i)` + `GetBlendShapeWeight(i)` でループ収集。`filter` が空でない場合は名前の部分一致で絞り込む。

### editor_set_blend_shape

BlendShape ウェイトを名前指定で設定する。ランタイム操作（Undo 対応なし）。

```
editor_set_blend_shape(
    hierarchy_path: str,       # SkinnedMeshRenderer を持つ GameObject
    name: str,                 # BlendShape 名
    weight: float              # 0-100
)
```

レスポンス:
```json
{
    "success": true,
    "severity": "info",
    "code": "EDITOR_CTRL_BLEND_SHAPE_SET_OK",
    "data": {
        "index": 42,
        "name": "vrc.blink",
        "before": 0.0,
        "after": 75.0
    }
}
```

実装: `sharedMesh.GetBlendShapeIndex(name)` で名前→インデックス解決 → `SetBlendShapeWeight(index, weight)`。

想定ワークフロー: `editor_get_blend_shapes` で一覧確認 → `editor_set_blend_shape` で値変更 → `editor_screenshot` でプレビュー → 反復。マテリアル調整と同じプレビューループ。

### editor_list_menu_items

プロジェクト内の `[MenuItem]` 属性を持つメソッドを列挙する。

```
editor_list_menu_items(
    prefix: str = ""           # パスプレフィックスフィルタ（例: "Tools/", "CONTEXT/"）
)
```

レスポンス:
```json
{
    "success": true,
    "severity": "info",
    "code": "EDITOR_CTRL_MENU_LIST_OK",
    "data": {
        "items": [
            { "path": "Tools/NDMF/Manual Bake", "shortcut": "" },
            { "path": "Tools/VRCFury/Force Refresh", "shortcut": "" }
        ],
        "count": 150
    }
}
```

実装方式: `[MenuItem]` 属性リフレクション。
1. `AppDomain.CurrentDomain.GetAssemblies()` で全ロード済みアセンブリを取得
2. 各アセンブリの全型 → 全 static メソッドを走査
3. `[MenuItem("path")]` 属性を持つメソッドのパスを収集
4. `prefix` が空でない場合は `StartsWith` で絞り込む

internal API (`Menu.GetMenuItems`) は使わない。Unity バージョン間の互換性を優先する。

### editor_execute_menu_item

Unity Editor のメニュー項目を実行する。

```
editor_execute_menu_item(
    menu_path: str             # "Tools/NDMF/Manual Bake" 等
)
```

レスポンス (成功):
```json
{
    "success": true,
    "severity": "info",
    "code": "EDITOR_CTRL_MENU_EXEC_OK",
    "message": "Menu item executed: Tools/NDMF/Manual Bake"
}
```

実装: `EditorApplication.ExecuteMenuItem(menu_path)` を呼ぶ。戻り値 `false`（メニュー項目が存在しない or validate で無効）はエラーとして返す。

注意: メニュー項目によってはダイアログを表示するものがある（例: ビルド確認）。ダイアログがブロックする場合の対処は Phase 2 では行わない。

## エラーコード

| コード | 発生条件 |
|--------|---------|
| `EDITOR_CTRL_NO_SMR` | hierarchy_path に SkinnedMeshRenderer が見つからない |
| `EDITOR_CTRL_BLENDSHAPE_NOT_FOUND` | 指定名の BlendShape が存在しない |
| `EDITOR_CTRL_MENU_NOT_FOUND` | menu_path のメニュー項目が存在しない or 実行不可 |

## 変更対象ファイル

| ファイル | 変更内容 |
|---------|---------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | 4 アクションのハンドラ追加 + `SupportedActions` 登録 |
| `prefab_sentinel/editor_bridge.py` | `SUPPORTED_ACTIONS` に 4 アクション追加 |
| `prefab_sentinel/mcp_server.py` | 4 つの `@server.tool()` 関数追加 |
| `tests/test_editor_bridge.py` | 4 ツールの unit test |

## テスト方針

- **Unit test**: `editor_bridge.py` の `send_action` 呼び出しパラメータ検証（既存パターンに従う）
- **統合テスト**: PF-TEST プロジェクトで実際に BlendShape get/set → screenshot 反復を確認
- **メニュー実行テスト**: `editor_list_menu_items` で `Tools/` を列挙 → `editor_execute_menu_item` で実行可能なメニューを呼ぶ
