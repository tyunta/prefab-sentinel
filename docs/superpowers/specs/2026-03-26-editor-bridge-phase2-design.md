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
- BlendShape の一括設定 (将来検討。ファイルプロトコルの往復コストが問題になったら追加)

## アクション名マッピング

| MCP ツール名 | bridge action 名 | C# ハンドラ |
|-------------|-----------------|------------|
| `editor_get_blend_shapes` | `get_blend_shapes` | `HandleGetBlendShapes` |
| `editor_set_blend_shape` | `set_blend_shape` | `HandleSetBlendShape` |
| `editor_list_menu_items` | `list_menu_items` | `HandleListMenuItems` |
| `editor_execute_menu_item` | `execute_menu_item` | `HandleExecuteMenuItem` |

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
        "total_entries": 800,
        "renderer_path": "Body"
    }
}
```

実装: `SkinnedMeshRenderer.sharedMesh.GetBlendShapeName(i)` + `GetBlendShapeWeight(i)` でループ収集。`filter` が空でない場合は名前の部分一致で絞り込む。`total_entries` はフィルタ前の全件数。

### editor_set_blend_shape

BlendShape ウェイトを名前指定で設定する。

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

実装: `sharedMesh.GetBlendShapeIndex(name)` で名前→インデックス解決 → `Undo.RecordObject(renderer, "Set BlendShape")` → `SetBlendShapeWeight(index, weight)`。

想定ワークフロー: `editor_get_blend_shapes` で一覧確認 → `editor_set_blend_shape` で値変更 → `editor_screenshot` でプレビュー → 反復。マテリアル調整と同じプレビューループ。

#### 永続化モデル

`Undo.RecordObject` を呼ぶため、変更は Editor セッション中は Undo/Redo 対象となり、Scene を保存すれば永続化される。Play モード遷移時は Editor の標準動作に従う（Scene 未保存なら変更は保持されるが、Undo 履歴はクリアされる）。`editor_set_material_property` と同じ永続化モデルを踏襲する。

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
        "total_entries": 150
    }
}
```

実装方式: `[MenuItem]` 属性リフレクション。
1. `AppDomain.CurrentDomain.GetAssemblies()` で全ロード済みアセンブリを取得
2. 各アセンブリの `GetTypes()` を呼ぶ。`ReflectionTypeLoadException` は `ex.Types` (null 除外) にフォールバックして継続する
3. 各型の static メソッドで `[MenuItem("path")]` 属性を持つものからパスを収集
4. validate メソッド（`[MenuItem("path", true)]` の第 2 引数が true）は除外する
5. `prefix` が空でない場合は `StartsWith` で絞り込む

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

#### 安全性

危険なメニュー項目の誤実行を防ぐため、以下のプレフィックスは deny-list として拒否する:

| Deny prefix | 理由 |
|------------|------|
| `File/New Scene` | 未保存の変更を破棄する |
| `File/New Project` | プロジェクトを破棄する |
| `Assets/Delete` | アセット削除 |

deny-list に該当するパスは `EDITOR_CTRL_MENU_DENIED` エラーを返す。deny-list は C# 側でハードコードし、拡張が必要になったら設定化する。

注意: メニュー項目によってはダイアログを表示するものがある（例: ビルド確認）。ダイアログがブロックする場合の対処は Phase 2 では行わない。

## C# DTO 変更

### EditorControlRequest 追加フィールド

| Python パラメータ | C# フィールド名 | 型 | デフォルト | 用途 |
|-----------------|---------------|------|----------|------|
| `filter` | `filter` | `string` | `""` | BlendShape 名フィルタ / メニュー prefix |
| `name` | `blend_shape_name` | `string` | `""` | BlendShape 名（`name` は既存と衝突回避） |
| `weight` | `blend_shape_weight` | `float` | `0f` | BlendShape ウェイト |
| `menu_path` | `menu_path` | `string` | `""` | メニュー項目パス |

`filter` は `editor_list_menu_items` の `prefix` パラメータにも流用する（Python 側で `filter=prefix` としてマッピング）。

### EditorControlData 追加フィールド

```csharp
// BlendShape
[Serializable]
public class BlendShapeEntry
{
    public int index;
    public string name;
    public float weight;
}

// EditorControlData に追加
public BlendShapeEntry[] blend_shapes;   // get_blend_shapes 用
public string renderer_path;             // get_blend_shapes 用
public int blend_shape_index;            // set_blend_shape 用
public string blend_shape_name;          // set_blend_shape 用
public float blend_shape_before;         // set_blend_shape 用
public float blend_shape_after;          // set_blend_shape 用

// Menu
[Serializable]
public class MenuItemEntry
{
    public string path;
    public string shortcut;
}

public MenuItemEntry[] menu_items;       // list_menu_items 用
```

`total_entries` は既存フィールドを流用する。

## エラーコード

| コード | 発生条件 |
|--------|---------|
| `EDITOR_CTRL_NO_SMR` | hierarchy_path に SkinnedMeshRenderer が見つからない |
| `EDITOR_CTRL_BLENDSHAPE_NOT_FOUND` | 指定名の BlendShape が存在しない |
| `EDITOR_CTRL_MENU_NOT_FOUND` | menu_path のメニュー項目が存在しない or 実行不可 |
| `EDITOR_CTRL_MENU_DENIED` | deny-list に該当するメニューパス |

## 変更対象ファイル

| ファイル | 変更内容 |
|---------|---------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | 4 ハンドラ + DTO 追加 + `SupportedActions` 登録 |
| `prefab_sentinel/editor_bridge.py` | `SUPPORTED_ACTIONS` に 4 アクション追加 |
| `prefab_sentinel/mcp_server.py` | 4 つの `@server.tool()` 関数追加 |
| `tests/test_editor_bridge.py` | 4 ツールの unit test + `test_all_actions_present` 更新 |
| `tests/test_mcp_server.py` | `EXPECTED_TOOLS` リスト更新 |

## テスト方針

### Unit test (既存パターンに従う)
- `SUPPORTED_ACTIONS` に 4 新アクションが含まれることを検証 (`test_all_actions_present` 更新)
- 各 MCP ツールの `send_action` 呼び出しパラメータ検証
- デフォルトパラメータのハンドリング (`filter=""`)
- `EXPECTED_TOOLS` リストに 4 ツール追加

### 統合テスト
- PF-TEST プロジェクトで BlendShape get → set → get の往復確認
- `editor_list_menu_items(prefix="Tools/")` で VRChat ツールのメニューが列挙されることを確認
- `editor_execute_menu_item` で安全なメニュー項目の実行確認
- deny-list のメニューが `EDITOR_CTRL_MENU_DENIED` を返すことを確認
