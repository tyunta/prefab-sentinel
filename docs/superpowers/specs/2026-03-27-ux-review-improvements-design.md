# UX レビュー改善設計

**日付:** 2026-03-27
**由来:** `docs/superpowers/specs/2026-03-27-prefab-sentinel-ux-review.md` — ForgottenTemple シーン構築で発覚した 7 件の改善
**構成:** Spec A (C# Bridge 修正 + 機能追加) → Spec B (インフラ改善)。Plan は A → B の順で 2 つ作成。

---

## 全体マップ

| Spec | # | 機能 | 工数 |
|------|---|------|------|
| **A** | 2 | ReflectionTypeLoadException 修正 + BridgeVersion 更新 | 極小 |
| **A** | 3 | 配列プロパティ読み書き (ArraySize + 要素設定) | 中 |
| **A** | 4 | editor_batch_add_component | 小 |
| **A** | 5 | プロトコルエラー詳細化 | 小 |
| **A** | 6 | editor_create_scene | 小 |
| **B** | 1 | Bridge 自動デプロイ | 中 |
| **B** | 9 | 配線検証自動化 (validate_all_wiring) | 小 |

---

## Spec A: C# Bridge 修正 + 機能追加

### A-2: ReflectionTypeLoadException 修正 + BridgeVersion

**問題:**
- `ResolveComponentType` (line 2356): `catch (System.ReflectionTypeLoadException)` — Unity のデフォルトアセンブリで未解決。完全修飾名 `System.Reflection.ReflectionTypeLoadException` が必要
- `HandleListMenuItems` (line 2765): 既に正しい完全修飾名を使用 — コードベース内で不整合
- `BridgeVersion` (line 20): `"0.5.82"` のまま — 実際の Plugin バージョンと乖離

**修正:**

```csharp
// line 2356: 修正
catch (System.Reflection.ReflectionTypeLoadException) { }

// line 20: pyproject.toml の version と同期
public const string BridgeVersion = "0.5.110";
```

BridgeVersion は今後も手動更新が必要（C# ファイルは bump-my-version の管理外）。Spec B の自動デプロイでバージョン不一致を検出する仕組みと組み合わせる。

### A-3: 配列プロパティ読み書き

**問題:** `HandleEditorSetProperty` の switch-case に `ArraySize` ケースがなく、`default:` で `TYPE_MISMATCH` エラーを返す。

**修正:** HandleEditorSetProperty に 2 つのケースを追加:

```csharp
case SerializedPropertyType.ArraySize:
    prop.intValue = int.Parse(v, CultureInfo.InvariantCulture);
    break;
```

これで `respawnPoints.Array.size = 6` が動作する。配列要素 (`respawnPoints.Array.data[0]`) は既存の ObjectReference ケースで処理可能（`FindProperty("respawnPoints.Array.data[0]")` が Unity API で有効）。

**追加で `FixedBufferSize` も同様に int 扱いにする。**

### A-4: editor_batch_add_component

**設計:** `editor_batch_create` と同パターンの Undo グループ付きバッチ。

**C# ハンドラ: HandleEditorBatchAddComponent**

リクエスト JSON:
```json
{
  "batch_operations_json": "[
    {\"hierarchy_path\":\"/Lobby/PlateA\",\"component_type\":\"BoxCollider\",\"properties_json\":\"[...]\"},
    {\"hierarchy_path\":\"/Lobby/PlateB\",\"component_type\":\"BoxCollider\"}
  ]"
}
```

DTO:
```csharp
[Serializable]
private sealed class BatchAddComponentOp
{
    public string hierarchy_path = string.Empty;
    public string component_type = string.Empty;
    public string properties_json = string.Empty;
}

[Serializable]
private sealed class BatchAddComponentArray { public BatchAddComponentOp[] items; }
```

ハンドラは各要素に対して HandleEditorAddComponent に合成リクエストを委譲（HandleEditorBatchSetProperty と同パターン）。

**Python MCP ツール:**
```python
editor_batch_add_component(
    operations: list[dict[str, Any]],
) -> dict[str, Any]
```

### A-5: プロトコルエラー詳細化

**現在 (line 313):**
```csharp
$"Expected protocol_version {ProtocolVersion}, got {request.protocol_version}."
```

**改善:**
```csharp
$"Bridge protocol v{request.protocol_version}, required v{ProtocolVersion}. " +
$"Update Bridge: copy tools/unity/*.cs from prefab-sentinel to Assets/Editor/PrefabSentinel/"
```

### A-6: editor_create_scene

**C# ハンドラ: HandleEditorCreateScene**

```csharp
var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
EditorSceneManager.SaveScene(scene, request.asset_path);
```

**パラメータ:**
- `asset_path`: 新シーンの保存先パス (例: `"Assets/Scenes/NewScene.unity"`)

既に `UnityPatchBridge.cs` (line 2095) で同 API の使用実績あり。

**Python MCP ツール:**
```python
editor_create_scene(
    scene_path: str,
) -> dict[str, Any]
```

### Spec A 変更ファイル

| ファイル | 変更 |
|---------|------|
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | BridgeVersion 更新、ReflectionTypeLoadException 修正、ArraySize/FixedBufferSize ケース追加、HandleEditorBatchAddComponent、HandleEditorCreateScene、プロトコルエラーメッセージ改善、SupportedActions +2、dispatch +2、DTO 2 クラス追加 |
| `prefab_sentinel/mcp_server.py` | editor_batch_add_component、editor_create_scene ツール追加 |
| `prefab_sentinel/editor_bridge.py` | SUPPORTED_ACTIONS +2 |
| `tests/test_editor_bridge.py` | テスト更新 |
| `tests/test_mcp_server.py` | テスト更新 (60 ツール) |
| `README.md` | ツールテーブル更新 |

---

## Spec B: インフラ改善

### B-1: Bridge 自動デプロイ

**動機:** Plugin 更新のたびに 6 つの C# ファイルを手動コピーする必要がある。バージョン不一致が頻発。

**設計:**

`activate_project` 時に Bridge バージョンチェック + 自動コピーオファー:

1. `session.activate()` で `project_root` が確定した時点で、`{project_root}/Assets/Editor/PrefabSentinel/` の `BridgeVersion` を検出
2. Plugin の `tools/unity/*.cs` のバージョンと比較
3. 不一致なら `diagnostics` に警告を含める + `data` に `bridge_update_available: true` を返す

**自動コピーは行わない** — 書き込み操作はユーザー合意が必要（CLAUDE.md の fail-fast ルール）。代わりに新 MCP ツール `deploy_bridge` を提供:

**MCP ツール: deploy_bridge**

```python
deploy_bridge(
    target_dir: str = "",  # デフォルト: {project_root}/Assets/Editor/PrefabSentinel/
) -> dict[str, Any]
```

処理:
1. `tools/unity/*.cs` を `target_dir` にコピー
2. コピー後に `editor_refresh` を送信 (AssetDatabase.Refresh)
3. コピーしたファイル一覧と旧/新バージョンをレスポンスに含める

**Bridge バージョン検出:**

Unity プロジェクト内の C# ファイルから `BridgeVersion` 定数を正規表現で抽出:

```python
import re
BRIDGE_VERSION_RE = re.compile(r'BridgeVersion\s*=\s*"([^"]+)"')
```

`activate_project` の diagnostics に含めることで、AI エージェントが自動的に `deploy_bridge` を呼ぶかユーザーに確認するかを判断できる。

### B-9: 配線検証自動化 (validate_all_wiring)

**動機:** `inspect_wiring` は 1 ファイルずつ。シーン全体の null 参照を一覧するには全ルートに対して個別に呼ぶ必要がある。

**設計:** 新 MCP ツール `validate_all_wiring`

```python
validate_all_wiring(
    asset_path: str = "",  # .unity シーンファイル。空 = activate されたスコープ全 .prefab/.unity
) -> dict[str, Any]
```

処理:
1. 対象ファイル収集: `asset_path` 指定時はそのファイルのみ。未指定時はスコープ内の全 `.prefab` + `.unity`
2. 各ファイルに対して `orchestrator.inspect_wiring()` を実行
3. null 参照のみをフィルタして集約
4. サマリーレスポンス: 総コンポーネント数、null 参照数、ファイル別内訳

**レスポンス例:**
```json
{
  "success": true,
  "code": "VALIDATE_WIRING_OK",
  "message": "Scanned 5 files: 120 components, 3 null references",
  "data": {
    "files_scanned": 5,
    "total_components": 120,
    "total_null_refs": 3,
    "null_refs_by_file": [
      {"file": "Assets/Scenes/Main.unity", "null_refs": 2, "components": 80},
      {"file": "Assets/Prefabs/Door.prefab", "null_refs": 1, "components": 12}
    ]
  }
}
```

### Spec B 変更ファイル

| ファイル | 変更 |
|---------|------|
| `prefab_sentinel/session.py` | `detect_bridge_version()` メソッド追加、`activate()` に Bridge バージョン診断追加 |
| `prefab_sentinel/mcp_server.py` | `deploy_bridge`、`validate_all_wiring` ツール追加 |
| `prefab_sentinel/orchestrator.py` | `validate_all_wiring()` メソッド追加 |
| `tests/test_session.py` | Bridge バージョン検出テスト |
| `tests/test_orchestrator.py` | validate_all_wiring テスト |
| `tests/test_mcp_server.py` | ツール登録テスト更新 |
| `README.md` | ツールテーブル更新 |

---

## 実装順序

1. **Plan A** を先に実装 (C# Bridge 修正 + 機能追加) — 6 タスク
2. **Plan B** を後に実装 (インフラ改善) — 3 タスク (session, orchestrator, MCP)

Plan A は PR 1 本、Plan B は PR 1 本。

## スコープ外

- シーン構築テンプレート (#8) — アプリケーション層の機能
- Bridge ファイルの自動バージョンバンプ — C# は bump-my-version 管理外のため手動維持
- `editor_open_scene` の未保存変更ハンドリング — Interactive mode では Unity がダイアログ表示
