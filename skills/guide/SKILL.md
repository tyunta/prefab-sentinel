---
name: guide
description: >-
  Unity Prefab/Scene/Asset の検査・編集・参照修復の MCP ツールリファレンス。
  .prefab, .unity, .asset, .mat, Prefab, Variant, 配線, 参照, 壊れた参照,
  broken reference, GUID, fileID, override, パッチ, patch plan, validate refs,
  validate structure, where-used, inspect variant, inspect wiring,
  inspect hierarchy, wiring, null参照, null reference, MonoBehaviour,
  階層, hierarchy, Transform整合, 孤立, orphan, 重複fileID, duplicate fileID
  のいずれかに該当する作業で使用する。
---

Unity プロジェクトで Prefab/Scene/Asset の検査・編集・参照修復を行う。

## インターフェース
MCP ツールを直接呼び出す（CLI は v0.4.0 で廃止済み）。
MCP サーバーは `prefab-sentinel-mcp` エントリポイントで起動する。

## 主要 MCP ツール

### 検査（read-only）
| ツール | 説明 |
|--------|------|
| `validate_refs` | 壊れた GUID/fileID 参照のスキャン。`scope`, `ignore_asset_guids` パラメータ |
| `inspect_variant` | Prefab Variant のオーバーライドチェーン分析 |
| `find_referencing_assets` | GUID/パスの参照元アセット検索 |
| `inspect_wiring` | MonoBehaviour フィールド配線検査（null参照・fileID不整合・重複参照） |
| `inspect_hierarchy` | GameObject 階層ツリー表示（深度制限、コンポーネント注釈対応） |
| `inspect_materials` | Renderer ごとのマテリアルスロット表示（override/inherited マーカー付き） |
| `validate_structure` | YAML 内部構造の検証（fileID 重複、Transform 整合性） |
| `get_unity_symbols` | アセットのシンボルツリー取得 |
| `find_unity_symbol` | 人間可読パスでオブジェクト検索 |
| `diff_unity_symbols` | Variant と Base の差分のみ返す |
| `list_serialized_fields` | C# スクリプトのシリアライズ対象フィールド一覧 |
| `validate_field_rename` | フィールドリネームの影響分析 |
| `check_field_coverage` | C# フィールドと YAML propertyPath の不一致検出 |

### 編集（write）
| ツール | 説明 |
|--------|------|
| `set_property` | シンボルパスでフィールド値を設定（dry-run/confirm） |
| `add_component` | GameObject にコンポーネント追加（dry-run/confirm） |
| `remove_component` | コンポーネント削除（dry-run/confirm） |
| `patch_apply` | パッチ計画の検証・適用（JSON 文字列入力、dry-run/confirm） |
| `revert_overrides` | Variant の特定オーバーライドを YAML レベルで削除（dry-run/confirm） |
| `validate_runtime` | UdonSharp コンパイル + ClientSim 実行検証 |

### Editor 操作（Editor Bridge 常駐が必須）
| ツール | 説明 |
|--------|------|
| `editor_screenshot` | Scene/Game ビューのスクリーンショット取得 |
| `editor_select` | Hierarchy 内の GameObject を選択（Prefab Stage 対応） |
| `editor_frame` | 選択オブジェクトを Scene ビューでフレーミング |
| `editor_camera` | Scene ビューのカメラ方向設定（yaw/pitch/distance） |
| `editor_list_children` | GameObject の子オブジェクト一覧 |
| `editor_list_materials` | ランタイムのレンダラーのマテリアルスロット一覧 |
| `editor_list_roots` | Scene / Prefab Stage のルートオブジェクト一覧 |
| `editor_get_material_property` | シェーダープロパティ値の読み取り |
| `editor_console` | Unity Console のログエントリを構造化データとして取得 |
| `editor_refresh` | AssetDatabase.Refresh() のトリガー |
| `editor_recompile` | C# スクリプト再コンパイルのトリガー |
| `editor_run_tests` | Unity 統合テストの実行 |
| `editor_instantiate` | Prefab を Scene にインスタンス化 |
| `editor_set_material` | マテリアルスロットの差し替え（Undo 対応） |
| `editor_delete` | GameObject の削除（Undo 対応） |

### セッション管理
| ツール | 説明 |
|--------|------|
| `activate_project` | プロジェクトスコープ設定 + キャッシュ warm |
| `get_project_status` | セッション状態の表示 |

## パッチ計画 JSON の構造

### リソース定義
```json
{
  "plan_version": 2,
  "resources": [
    {
      "id": "res1",
      "kind": "prefab",
      "path": "Assets/.../Target.prefab",
      "mode": "open"
    }
  ],
  "ops": [...],
  "postconditions": []
}
```
- `kind`: `prefab`, `scene`, `asset`, `material`, `json`
- `mode`: `open`（既存アセット編集）または `create`（新規作成）

### open モード — 既存アセットのプロパティ編集
```json
{"resource": "res1", "op": "set", "component": "<TypeName>", "path": "m_SomeField", "value": 42}
{"resource": "res1", "op": "insert_array_element", "component": "<TypeName>", "path": "m_Array", "index": 0, "value": "item"}
{"resource": "res1", "op": "remove_array_element", "component": "<TypeName>", "path": "m_Array", "index": 0}
```
- `component` は **型名セレクタ** で指定する（例: `SkinnedMeshRenderer`, `UnityEngine.MeshFilter`, `MyNamespace.MyComponent`）
- 同型コンポーネントが複数存在する場合は `TypeName@/hierarchy/path` で曖昧性を解消する（例: `SkinnedMeshRenderer@/Body`）
- **注意**: YAML の `m_Modifications.target.fileID` や数値 fileID は component セレクタとして使用できない。C# ブリッジは型名でコンポーネントを検索する
- Unity SerializedProperty 経由で操作するため、path は Unity 内部パスに従う
- 既存プロパティの値変更・配列操作のみ。構造的な追加（GameObject/Component）は不可
- `ObjectReference` の `value` は `{"guid": "...", "fileID": 10207}` または `{"guid": "...", "file_id": 10207}` の両形式を受け付ける（Unity ネイティブ `fileID` を優先）
- Unity 組み込みリソースの参照も GUID+fileID で解決可能。GUID はロケーションで異なる:
  - `0000000000000000e000000000000000` — `Library/unity default resources`（メッシュ: Sphere, Cube 等）
  - `0000000000000000f000000000000000` — `Resources/unity_builtin_extra`（マテリアル: Default-Material, シェーダ等）
- 既知ビルトインアセットの fileID⇔名前マッピングは `prefab_sentinel/builtin_assets.py` に集約。`resolve_builtin_reference(guid, file_id)` で名前解決、`BUILTIN_SPHERE_MESH` 等の定数も同モジュールからインポートする

### create モード — 新規 Prefab のゼロ作成（Unity 環境必須）

ハンドルシステム: 各 op の `"result": "handle名"` で作成したオブジェクトに名前を付け、後続 op からプレーン文字列 `"handle名"` で参照する。`"$handle名"` でも可（先頭 `$` は自動除去）。ルートは自動的に `"root"` ハンドルを持つ。

```json
{
  "plan_version": 2,
  "resources": [
    {
      "id": "res1",
      "kind": "prefab",
      "path": "Assets/.../NewPrefab.prefab",
      "mode": "create"
    }
  ],
  "ops": [
    {"resource": "res1", "op": "create_prefab", "name": "Circle"},
    {"resource": "res1", "op": "create_game_object", "name": "Sphere_00", "parent": "root", "result": "sphere0"},
    {"resource": "res1", "op": "add_component", "target": "sphere0", "type": "MeshFilter", "result": "mf0"},
    {"resource": "res1", "op": "add_component", "target": "sphere0", "type": "MeshRenderer", "result": "mr0"},
    {"resource": "res1", "op": "set", "target": "mf0", "path": "m_Mesh", "value": {"fileID": 10207, "guid": "0000000000000000e000000000000000", "type": 0}},
    {"resource": "res1", "op": "save"}
  ],
  "postconditions": []
}
```

**create モード op 一覧:**

| op | 必須フィールド | 省略可 | 説明 |
|---|---|---|---|
| `create_prefab` | — | `name`, `result` | Prefab ルート作成。`name` 省略時はファイル名から自動命名。`"root"` ハンドル自動付与 |
| `create_root` | `name` | `result` | `create_prefab` の別名（`name` 必須版）。**`create_prefab` と排他で、どちらか一方のみ使用可** |
| `create_game_object` | `name`, `parent` | `result` | 子 GameObject 追加。parent はハンドル文字列 |
| `add_component` | `target`, `type` | `result` | コンポーネント追加。target は GO ハンドル文字列 |
| `find_component` | `target`, `type` | `result` | 既存コンポーネントのハンドル取得 |
| `remove_component` | `target` | — | コンポーネント削除。target はコンポーネントハンドル文字列 |
| `rename_object` | `target`, `name` | — | GameObject リネーム |
| `reparent` | `target`, `parent` | — | 親子関係の変更（root は不可） |
| `set` | `target`, `path`, `value` | — | プロパティ値の設定 |
| `insert_array_element` | `target`, `path`, `index`, `value` | — | 配列要素の挿入 |
| `remove_array_element` | `target`, `path`, `index` | — | 配列要素の削除 |
| `save` | — | — | 保存（最終 op として1回のみ） |

### scene モード — Scene 編集（Unity 環境必須）

Scene 編集は `open_scene` で開始し、`save_scene` で終了する。この 2 つは必須。
```json
{
  "plan_version": 2,
  "resources": [
    {"id": "s1", "kind": "scene", "path": "Assets/Scenes/Main.unity", "mode": "open"}
  ],
  "ops": [
    {"resource": "s1", "op": "open_scene"},
    {"resource": "s1", "op": "find_component", "target": "$scene", "type": "UnityEngine.Light", "result": "$light"},
    {"resource": "s1", "op": "set", "target": "$light", "path": "m_Intensity", "value": 2.5},
    {"resource": "s1", "op": "save_scene"}
  ]
}
```
- `open_scene` は最初の op として必須。`save_scene` は最後の op として必須（各 1 回のみ）
- Scene 内のオブジェクト操作は create モードと同じハンドルシステムを使用（`find_component`, `set`, `insert_array_element`, `remove_array_element`）
- `create_game_object`, `add_component` で Scene への新規追加も可能

## ワークフロー選択
- **Prefab 編集時**（open モード）: `validate_structure` → `patch_apply`(dry-run) → `patch_apply`(confirm) → `validate_refs` → `validate_runtime`
- **Prefab 新規作成時**（create モード）: パッチ計画作成 → `patch_apply`(dry-run) → `patch_apply`(confirm) → `validate_structure` → `validate_refs`
- **Unity Editor 起動中の適用**: Editor Bridge セットアップ → `UNITYTOOL_BRIDGE_MODE=editor` → 通常通り `patch_apply` / `validate_runtime`
- **Editor リモート操作**: `editor_select` → `editor_frame` で対象を表示 → `editor_screenshot` で視覚確認。スクショはトリアージの起点として使い、データソースにしない（必ず `inspect_wiring` / `validate_refs` で裏取りする）
- **Console ログ取得**: `editor_console` でリアルタイムログをテキスト取得。batchmode 後の Editor.log 読みではなく、Editor 起動中のバッファからの取得
- **フィールド配線検査**: `inspect_wiring` → null参照・fileID不整合の特定、重複は same-component（WARNING）/ cross-component（INFO）に分類。Variant ファイルではベース Prefab のコンポーネントを自動解析
- **階層構造の確認**: `inspect_hierarchy` → ツリー構造・コンポーネント配置の把握。Variant ファイルではベース Prefab の階層を表示し、オーバーライド付きノードに `[overridden: N]` マーカーを付与
- **マテリアル構成の確認**: `inspect_materials` → Renderer ごとのマテリアルスロット一覧。Variant チェーンを考慮し、各スロットが `overridden` / `inherited` かを表示
- **内部構造の検証**: `validate_structure` → fileID重複・Transform整合性・参照欠損の検出
- **壊れた参照の修復**: `validate_refs` → `find_referencing_assets` → `ignore_asset_guids` で偽陽性を除外 → safe_fix / decision_required
- **ランタイムエラー調査**: `validate_runtime` → ログ分類 → アセット特定 → 修正提案
- **オーバーライドの削除**: `revert_overrides` → Variant の特定の Modification 行を YAML から除去。dry-run → confirm のゲート付き
- **見た目の反復調整**: `editor_set_material` でスロット差し替え → `editor_camera` でアングル調整 → `editor_screenshot` で確認 → 確定後に `revert_overrides` / `patch_apply` で永続化
- **Variant チェーン値の追跡**: `inspect_variant` で各プロパティ値がどの Prefab で設定されたかを表示。3段 Variant (Base → Mid → Leaf) の場合、各値に `origin_path` と `origin_depth` が付与される
- **アクセサリ / 衣装の差し替え**: `editor_delete` で旧オブジェクトを削除 → `editor_instantiate` で新 Prefab を追加（下記 MA/NDMF セクション参照）
- **ランタイム階層の確認**: `editor_list_children` でシーン実行中の子オブジェクト一覧を取得。`inspect_hierarchy` はファイルベースだが、こちらは Prefab Instance 内のネスト構造も表示
- **ランタイムマテリアル確認**: `editor_list_materials` で Unity API から直接 Renderer のマテリアルスロット一覧を取得。`inspect_materials` がオフラインで Variant/FBX チェーンを解決できない場合の代替。各スロットの `material_guid` を `editor_set_material` に直接使用可能

## VRChat アバター着せ替えワークフロー（MA/NDMF）

MA (Modular Avatar) / NDMF ベースの VRChat アバターでは、コスメティックや衣装の差し替えに Variant チェーン分析は**不要**。以下のパターンで完結する。

### MA/NDMF パターン（着せ替え）
- コスメティック（ネイル、リボン、アクセサリ等）はアバタールートの**子 Prefab として追加するだけ**で、MA がビルド時にマージする
- 差し替え = 旧 Prefab を `editor_delete` で削除 + 新 Prefab を `editor_instantiate` で追加
- Variant チェーンの fileID 比較や m_SourcePrefab 書き換えは**不要**
- 衣装差し替えも同様: 旧衣装 Prefab を削除 → 新衣装 Prefab を子として追加

### 判断フロー
```
着せ替え/差し替え作業？
├─ MA/NDMF ベース → 子オブジェクト操作（instantiate/delete）で完結
│   └─ Variant チェーン分析は不要
└─ 非 MA/NDMF or マテリアル修正 → Variant 分析が有効
    └─ inspect_variant / inspect_materials で調査
```

### Variant チェーン分析が必要なケース
- マテリアルオーバーライドの調査・修正（`inspect_variant` + `inspect_materials`）
- プロパティ値の追跡（origin 付きで各プロパティがどのレベルの Prefab で設定されたか確認）
- Variant 固有の壊れた参照の修復

## 関連スキル（ワークフロー自動化）

本 guide は MCP ツールリファレンス。以下のスキルは複数ツールを組み合わせたゲート付きワークフローを提供する。

| スキル | 用途 | トリガー |
|--------|------|---------|
| `/prefab-sentinel:variant-safe-edit` | Prefab/Scene/Asset の安全な編集。preflight → dry-run → confirm → validate のフルパイプライン | パッチ適用、Prefab 編集全般 |
| `/prefab-sentinel:prefab-reference-repair` | 壊れた参照の検出・修復。ignore-guid ポリシーと decision_required ゲート付き | `validate_refs` で壊れた GUID/fileID が検出されたとき |
| `/prefab-sentinel:udon-log-triage` | ランタイム例外・Udon/ClientSim ログエラーの分類→アセット特定→修正提案 | ランタイム例外やログベースのリグレッション発生時 |

## 既知の制約と回避策

### add_component の UdonSharp 自動 backing

create モードの `add_component` で UdonSharpBehaviour 型を指定すると、backing UdonBehaviour が自動生成される（リフレクション検出、`programSource` 自動設定、`_udonSharpBackingUdonBehaviour` 自動配線）。backing は `backing_<handle>` でハンドル登録される。open モードでは `add_component` は使えない（YAML 直接編集か Unity Add Component を使う）。

### add_component は open モード不可

`add_component` は create モード専用。既存 Prefab にコンポーネントを追加する bridge 操作は存在しない。dry-run で `"create-mode operation"` エラーが出る。既存 Prefab へのコンポーネント追加は YAML 直接編集で行う。

### 配列パスは `.Array.data` で終わる必要がある

`insert_array_element` / `remove_array_element` のパスは `globalSwitches.Array.data` のように `.Array.data` サフィックスが必須。`globalSwitches` だけでは dry-run で `schema_error` になる。Unity の SerializedProperty が配列要素に `.Array.data[n]` パスを使うため。

### ObjectReference の value 指定方法

- **create モード**: `{"handle": "c_cam"}` 形式でハンドル参照を使える（`set` / `insert_array_element` の `value` に指定）。dry-run でハンドルの存在が検証される。
- **open モード**: `{"guid": "...", "fileID": ...}` 形式または null を使う。ハンドル文字列（`"$root"`, `"c_camera"` 等）を直接渡すとランタイムエラー。dry-run で `handle_in_value` 警告が出る。

### UdonSharp Prefab 新規作成（推奨手順）

1. **create plan**: 階層 + UdonSharp 含む全コンポーネント + `{"handle": "..."}` でハンドル配線 + 配列挿入 + save（1プランで完結）
2. **validate**: `validate_structure` → `inspect_wiring` → `validate_refs`

### UdonSharp の二重構造

UdonSharp コンポーネントは 2 つの MonoBehaviour で構成される。片方だけでは "Selected U# behaviour is not setup" エラーになる。

1. **UdonSharpBehaviour** — C# クラスのフィールドを持つ。`_udonSharpBackingUdonBehaviour` で backing を参照
2. **Backing UdonBehaviour** — `m_Script: {guid: 45115577ef41a5b4ca741ed302693907}` 固定。`serializedProgramAsset` と `programSource` を持つ

## 判断ルール
- `safe_fix`: 一意で決定的な修正のみ自動適用可。
- `decision_required`: ユーザー合意まで保留。
- `error` / `critical` が出たら停止し、修正または判断待ちへ回す。
- Unity 環境がない場合は dry-run / 検査までで停止する。
- `patch_apply` の confirm モードでは `change_reason` を必須とする（監査ログ）。

## Unity ブリッジセットアップ

### アーキテクチャ概要

パッチ実適用とランタイム検証は Unity batchmode を経由する。MCP → `tools/unity_patch_bridge.py`（stdin/stdout JSON プロトコル）→ Unity batchmode → `PrefabSentinel.UnityPatchBridge.ApplyFromJson`。dry-run は Unity 不要だが、confirm による実適用と `validate_runtime` は Unity 環境が必須。

### セットアップ手順

1. **C# ブリッジを Unity プロジェクトにコピー**
   ```bash
   # パッチ適用ブリッジ（必須）
   cp tools/unity/PrefabSentinel.UnityPatchBridge.cs <UnityProject>/Assets/Editor/

   # ランタイム検証ブリッジ（validate_runtime を使う場合）
   cp tools/unity/PrefabSentinel.UnityRuntimeValidationBridge.cs <UnityProject>/Assets/Editor/

   # Editor 制御ブリッジ（editor_* ツールを使う場合）
   cp tools/unity/PrefabSentinel.UnityEditorControlBridge.cs <UnityProject>/Assets/Editor/
   ```

2. **環境変数を設定**

   Windows (cmd):
   ```cmd
   set UNITYTOOL_PATCH_BRIDGE=python tools/unity_patch_bridge.py
   set UNITYTOOL_UNITY_COMMAND=C:/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe
   set UNITYTOOL_UNITY_PROJECT_PATH=D:/git/your-unity-project
   ```

   macOS / Linux:
   ```bash
   export UNITYTOOL_PATCH_BRIDGE="python tools/unity_patch_bridge.py"
   export UNITYTOOL_UNITY_COMMAND="/Applications/Unity/Hub/Editor/<version>/Unity.app/Contents/MacOS/Unity"
   export UNITYTOOL_UNITY_PROJECT_PATH="/path/to/your-unity-project"
   ```

   WSL:
   ```bash
   export UNITYTOOL_PATCH_BRIDGE="python tools/unity_patch_bridge.py"
   export UNITYTOOL_UNITY_COMMAND="/mnt/c/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe"
   export UNITYTOOL_UNITY_PROJECT_PATH="D:/git/your-unity-project"  # Windows パスでも WSL パスでも可
   ```
   - `UNITYTOOL_UNITY_PROJECT_PATH` は Windows パス（`D:/...`）と WSL パス（`/mnt/d/...`）の両方を受け付ける（`prefab_sentinel/wsl_compat.py` が `wslpath` 経由で自動変換）
   - `UNITYTOOL_UNITY_COMMAND` のスペース入りパスは引用符なしでも自動復元される（プログレッシブ・ジョイン）
   - Unity.exe（`.exe`）実行時のみ引数パスを Windows 形式に自動変換。非 `.exe` コマンドでは変換しない

### Bridge セットアップ手順（インタラクティブ）

ユーザーが Bridge セットアップを依頼した場合:

1. ユーザーに Unity プロジェクトパスと Unity 実行ファイルパスを確認する
2. `${CLAUDE_PLUGIN_ROOT}/tools/unity/` から C# ファイルを `<UnityProject>/Assets/Editor/` にコピーする
3. 環境変数を設定する（ユーザーのシェル設定に追記、または Claude Code settings.json に設定）
4. `validate_runtime` MCP ツールで疎通確認する

### 環境変数リファレンス

| 変数名 | 説明 | 必須 | デフォルト |
|--------|------|------|-----------|
| `UNITYTOOL_PATCH_BRIDGE` | ブリッジコマンド（例: `python tools/unity_patch_bridge.py`） | 実適用時 | — |
| `UNITYTOOL_UNITY_COMMAND` | Unity 実行ファイルパス | 実適用時 | — |
| `UNITYTOOL_UNITY_PROJECT_PATH` | Unity プロジェクトルート | 実適用時 | — |
| `UNITYTOOL_UNITY_EXECUTE_METHOD` | パッチブリッジの Unity エントリポイント | — | `PrefabSentinel.UnityPatchBridge.ApplyFromJson` |
| `UNITYTOOL_RUNTIME_EXECUTE_METHOD` | ランタイム検証の Unity エントリポイント | — | `PrefabSentinel.UnityRuntimeValidationBridge.RunFromJson` |
| `UNITYTOOL_UNITY_TIMEOUT_SEC` | Unity batchmode タイムアウト（秒） | — | パッチ: `120` / ランタイム: `300` |
| `UNITYTOOL_UNITY_LOG_FILE` | Unity ログファイルパス（指定時はログを解析） | — | — |
| `UNITYTOOL_BRIDGE_MODE` | ブリッジモード: `batchmode`（デフォルト） / `editor` | — | `batchmode` |
| `UNITYTOOL_BRIDGE_WATCH_DIR` | Editor Bridge の watch ディレクトリ（`editor` モード時必須） | editor 時 | — |

### Editor Bridge モード（Unity エディタ起動中のパッチ適用）

Unity Editor が起動中は batchmode と排他ロックが発生する。Editor Bridge を使うと、エディタを閉じずにパッチ適用・ランタイム検証を実行できる。

**セットアップ:**
1. `tools/unity/PrefabSentinel.EditorBridge.cs` を `<UnityProject>/Assets/Editor/` にコピー
2. Unity Editor で `PrefabSentinel > Editor Bridge` ウィンドウを開き、watch ディレクトリを設定して有効化
3. 環境変数を設定:
   ```bash
   export UNITYTOOL_BRIDGE_MODE=editor
   export UNITYTOOL_BRIDGE_WATCH_DIR=/tmp/sentinel-bridge  # Editor と同じディレクトリ
   ```
4. 通常通り `patch_apply` / `validate_runtime` MCP ツールを実行（Unity コマンドの設定は不要）

**アーキテクチャ:** Python → `{uuid}.request.json` をアトミック書き込み → EditorBridge がポーリング検出 → `UnityPatchBridge.ApplyFromPaths` or `UnityRuntimeValidationBridge.RunFromPaths` → `{uuid}.response.json` をアトミック書き込み → Python がポーリング読み取り

### ブリッジコマンド許可リスト

`UNITYTOOL_PATCH_BRIDGE` のコマンドヘッド（先頭トークン）は許可リスト制。許可されているコマンド: `python`, `python3`, `py`, `uv`, `uvx` およびそれらの `.exe` 版、`prefab-sentinel-unity-bridge`, `prefab-sentinel-unity-serialized-object-bridge`。
