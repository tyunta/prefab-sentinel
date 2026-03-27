# Bridge UX Improvements 設計

**日付:** 2026-03-27
**由来:** report_20260327_v085_full_mcp_workflow.md — 型名解決不安定、親子関係変更不可、add_component レスポンス不足
**アプローチ:** C# EditorControlBridge 既存ハンドラ修正 + 新ハンドラ 1 件 + Python MCP ラッパー

---

## 概要

3 件の UX 改善:

1. **`ResolveComponentType` 改善** — 短縮名 (`BoxCollider`) で全アセンブリの Component 派生型を検索
2. **`editor_set_parent` 新設** — 既存 GO の親子関係を変更
3. **`editor_add_component` レスポンス拡張** — 成功時に完全修飾型名を返す

---

## 1. ResolveComponentType 改善

### 問題

`editor_add_component` で `BoxCollider` が `TYPE_NOT_FOUND` になる。原因:

- ステップ 2 の `asm.GetType("BoxCollider")` は完全修飾名を要求するため短縮名では解決不可
- ステップ 3 の `UnityEngine.{name}, UnityEngine.CoreModule` は CoreModule のみ。`BoxCollider` は `UnityEngine.PhysicsModule` に所属

`MeshFilter` / `MeshRenderer` が成功するのは `UnityEngine.CoreModule` に所属しているため。

### 修正

ステップ 3 を削除し、代わりに「全アセンブリの exported 型から `Component` 派生 + `type.Name` 一致」を検索する。

```csharp
private static System.Type ResolveComponentType(string typeName)
{
    // 1. Fully qualified name (fastest path)
    var t = System.Type.GetType(typeName);
    if (t != null && typeof(Component).IsAssignableFrom(t))
        return t;

    // 2. Search all loaded assemblies by full name
    foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
    {
        t = asm.GetType(typeName);
        if (t != null && typeof(Component).IsAssignableFrom(t))
            return t;
    }

    // 3. Search all loaded assemblies by simple name (handles short names
    //    like "BoxCollider" that live in UnityEngine.PhysicsModule etc.)
    foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
    {
        try
        {
            foreach (var type in asm.GetExportedTypes())
            {
                if (type.Name == typeName && typeof(Component).IsAssignableFrom(type))
                    return type;
            }
        }
        catch (System.ReflectionTypeLoadException) { }
    }

    return null;
}
```

`GetExportedTypes()` は一部アセンブリで `ReflectionTypeLoadException` を投げる可能性があるため try-catch で保護。パフォーマンス: MCP 呼び出し頻度では問題なし。

---

## 2. editor_set_parent

### 動機

`editor_select` + `Create Empty Child` で子 GO を作れるが、既存 GO の親子関係変更には対応できない。

### C# ハンドラ: HandleEditorSetParent

**リクエストフィールド:**

| フィールド | 型 | 既存/新規 | 用途 |
|-----------|---|----------|------|
| `hierarchy_path` | string | 既存 | 移動する GO |
| `new_name` | string | 既存 (再利用) | 新しい親の hierarchy path。空文字 = シーンルートに移動 |

`new_name` フィールドを親パスとして再利用（新フィールド追加を避ける）。Python 側で `parent_path` パラメータ名にマッピング。

**処理フロー:**

1. バリデーション: `hierarchy_path` 必須
2. `GameObject.Find(hierarchy_path)` で子 GO 取得
3. `new_name` が非空なら `GameObject.Find(new_name)` で親 GO 取得。空なら `null` (ルートに移動)
4. `Undo.SetTransformParent(child.transform, parent?.transform, "PrefabSentinel: SetParent")`
5. 成功レスポンス

**エラーコード:**

| コード | 条件 |
|--------|------|
| `EDITOR_CTRL_SET_PARENT_NO_PATH` | hierarchy_path 未指定 |
| `EDITOR_CTRL_SET_PARENT_NOT_FOUND` | 子 GO が見つからない |
| `EDITOR_CTRL_SET_PARENT_PARENT_NOT_FOUND` | 親 GO が見つからない |

### Python MCP ツール

```python
@server.tool()
def editor_set_parent(
    hierarchy_path: str,
    parent_path: str = "",
) -> dict[str, Any]:
    """Set the parent of a GameObject in the scene hierarchy (Undo-able).

    Args:
        hierarchy_path: Hierarchy path to the child GameObject to move.
        parent_path: Hierarchy path to the new parent. Empty = move to scene root.
    """
    return send_action(
        action="editor_set_parent",
        hierarchy_path=hierarchy_path,
        new_name=parent_path,  # reuses new_name field for parent path
    )
```

---

## 3. editor_add_component レスポンス拡張

### 変更

成功レスポンスの `data` に完全修飾型名を追加。

現在:
```csharp
var resp = BuildSuccess("EDITOR_CTRL_ADD_COMP_OK",
    $"Added {compType.Name} to {request.hierarchy_path}",
    data: new EditorControlData
    {
        selected_object = go.name,
        executed = true,
        read_only = false,
    });
```

変更後:
```csharp
var resp = BuildSuccess("EDITOR_CTRL_ADD_COMP_OK",
    $"Added {compType.FullName} to {request.hierarchy_path}",
    data: new EditorControlData
    {
        selected_object = go.name,
        asset_path = compType.FullName,  // full qualified type name
        executed = true,
        read_only = false,
    });
```

`asset_path` フィールドを完全修飾型名の返却に流用。`message` も `FullName` に変更して短縮名入力時に解決された型名がわかるようにする。

---

## 変更ファイル一覧

| ファイル | 変更 |
|---------|------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | ResolveComponentType 書き換え、HandleEditorSetParent 追加、HandleEditorAddComponent レスポンス変更、SupportedActions +1、dispatch +1 |
| `prefab_sentinel/mcp_server.py` | `editor_set_parent` ツール追加 |
| `prefab_sentinel/editor_bridge.py` | SUPPORTED_ACTIONS に `editor_set_parent` 追加 |
| `tests/test_editor_bridge.py` | SUPPORTED_ACTIONS テスト更新 |
| `tests/test_mcp_server.py` | ツール登録テスト更新 (51 → 52) |

## スコープ外

- ブリッジ自動デプロイ機能 — 別 spec で対応
- `editor_add_component` 失敗時の候補提示 — YAGNI
