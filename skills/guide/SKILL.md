---
name: guide
description: >-
  Unity Prefab/Scene/Asset の検査・編集・参照修復の CLI リファレンス。
  .prefab, .unity, .asset, .mat, Prefab, Variant, 配線, 参照, 壊れた参照,
  broken reference, GUID, fileID, override, パッチ, patch plan, validate refs,
  validate structure, where-used, inspect variant, inspect wiring,
  inspect hierarchy, wiring, null参照, null reference, MonoBehaviour,
  階層, hierarchy, Transform整合, 孤立, orphan, 重複fileID, duplicate fileID
  のいずれかに該当する作業で使用する。
---

Unity プロジェクトで Prefab/Scene/Asset の検査・編集・参照修復を行う。

## 呼び出し方
```bash
uvx --from "${CLAUDE_PLUGIN_ROOT}" prefab-sentinel <command>
```
以下のコマンド例では `prefab-sentinel` を上記で読み替える。

## 主要コマンド
```bash
# 参照検査（壊れた GUID/fileID を検出、.anim/.controller/.overridecontroller を含む全 Unity YAML 対応）
# top_missing_asset_guids に asset_name（GUID→アセットパス）がベストエフォートで付記される
prefab-sentinel validate refs --scope "Assets/YourScope"

# Prefab Variant のオーバーライド一覧
prefab-sentinel inspect variant --path "Assets/...Variant.prefab"

# アセットの参照元を検索
prefab-sentinel inspect where-used --asset-or-guid "Assets/SomeAsset.prefab" --scope "Assets"

# MonoBehaviour フィールド配線検査（null参照・内部fileID不整合・重複参照）
# 出力には game_object_name, script_name が付与される（GUID→.cs ファイル名の自動解決）
# 重複参照は [same-component]（WARNING: バグの可能性）と [cross-component]（INFO: ハブパターン）に分類される
prefab-sentinel inspect wiring --path "Assets/...SomeAsset.prefab"

# UdonSharp コンポーネントのみに絞って検査
prefab-sentinel inspect wiring --path "Assets/...SomeAsset.prefab" --udon-only

# 階層構造の可視化（GameObject ツリー + コンポーネント注釈）
prefab-sentinel inspect hierarchy --path "Assets/...SomeAsset.prefab"

# 深さ制限付き、コンポーネント非表示
prefab-sentinel inspect hierarchy --path "Assets/...SomeAsset.prefab" --depth 2 --no-components

# マテリアルスロット一覧（Renderer ごとのスロット + Variant チェーンのオーバーライド/継承マーカー）
prefab-sentinel inspect materials --path "Assets/...SomeAsset.prefab"
prefab-sentinel inspect materials --path "Assets/...SomeAsset.prefab" --format md

# YAML 内部構造の整合性検証（fileID重複・Transform双方向整合・コンポーネント参照・孤立Transform）
# stripped Transform（Prefab Variant 由来）は自動スキップされ偽陽性にならない
prefab-sentinel validate structure --path "Assets/...SomeAsset.prefab"

# Variant の特定オーバーライドを削除（YAML レベルの Modification 行を除去）
prefab-sentinel patch revert --path "Assets/...Variant.prefab" \
  --target "1234567890" --property "m_Materials.Array.data[0]" --dry-run
prefab-sentinel patch revert --path "Assets/...Variant.prefab" \
  --target "1234567890" --property "m_Materials.Array.data[0]" \
  --confirm --change-reason "不要なマテリアルオーバーライドを削除"

# パッチ計画のドライラン
prefab-sentinel patch apply --plan "patch_plan.json" --dry-run

# パッチ適用（Unity 環境必須、--change-reason と --out-report は必須）
prefab-sentinel patch apply --plan "patch_plan.json" \
  --confirm --out-report "report.json" --change-reason "理由"

# パッチ適用 + preflight 参照検査 + ランタイム検証
prefab-sentinel patch apply --plan "patch_plan.json" \
  --confirm --change-reason "理由" --out-report "report.json" \
  --scope "Assets/YourScope" \
  --runtime-scene "Assets/Scenes/Smoke.unity"

# パッチ計画の自動生成（circle レイアウト）
prefab-sentinel patch generate circle \
  --output Assets/Circle.prefab --root-name Circle \
  --count 12 --radius 3.0 --out circle_plan.json

# 生成計画をそのまま dry-run に渡す
prefab-sentinel patch generate circle \
  --output Assets/Ring.prefab --count 8 --radius 2.0 \
  --child-name Node --scale 0.5 --axis xy --out /tmp/ring.json
prefab-sentinel patch apply --plan /tmp/ring.json --dry-run

# パッチ計画の改ざん検知
prefab-sentinel patch hash --plan "patch_plan.json"
prefab-sentinel patch sign --plan "patch_plan.json"
prefab-sentinel patch attest --plan "patch_plan.json" --out "attestation.json"
prefab-sentinel patch verify --plan "patch_plan.json" --attestation-file "attestation.json"

# ignore 候補 GUID の提案（偽陽性排除用）
# 候補出力には asset_name（GUID→アセットパス）がベストエフォートで付記される
prefab-sentinel suggest ignore-guids --scope "Assets/YourScope" --min-occurrences 50

# ランタイム検証（Unity 環境必須）
prefab-sentinel validate runtime --scene "Assets/Scenes/Smoke.unity"

# レポート変換（md / json / csv）
prefab-sentinel report export --input "report.json" --format md --out "report.md"
prefab-sentinel report export --input "report.json" --format csv --out "report.csv"
prefab-sentinel report export --input "report.json" --format csv --out "report.csv" --csv-include-summary

# Editor 操作（Editor Bridge 常駐が必須）
prefab-sentinel editor screenshot --view scene        # Scene ビューのスクショ取得
prefab-sentinel editor screenshot --view game          # Game ビューのスクショ取得
prefab-sentinel editor select --path "/Canvas/Panel"   # Hierarchy 上のオブジェクトを選択
prefab-sentinel editor select --path "Hair_Base" --prefab-stage "Assets/.../Variant.prefab"  # Prefab Stage 内のオブジェクトを選択
prefab-sentinel editor frame                           # 選択オブジェクトを Scene ビュー中央に表示
prefab-sentinel editor frame --zoom 0.5                # 近づく（小さい値 = ズームイン、SceneView.size にマップ）
prefab-sentinel editor frame --distance 2.0            # 離れる（大きい値 = ズームアウト）。--zoom のエイリアス
prefab-sentinel editor instantiate --prefab "Assets/Prefabs/Mic.prefab"  # Prefab を Scene に配置
prefab-sentinel editor instantiate --prefab "Assets/Prefabs/Mic.prefab" --parent "/Canvas" --position 0,1.5,0
prefab-sentinel editor ping --asset "Assets/Prefabs/Mic.prefab"  # Project ウィンドウでアセットをハイライト
prefab-sentinel editor console                                   # Console ログをテキスト取得
prefab-sentinel editor console --filter error                    # エラー/例外のみ取得
prefab-sentinel editor console --max-entries 50 --since 60       # 直近60秒の最大50件
prefab-sentinel editor console --classify                        # 取得後に classify_errors() でパターンマッチ
prefab-sentinel editor refresh                                   # AssetDatabase.Refresh() をトリガー
prefab-sentinel editor set-material --renderer "/Body/Mesh" --index 1 --material-guid "dbb963022c044..."  # マテリアルスロット差し替え（非破壊、Undo対応）
prefab-sentinel editor delete --path "/AvatarRoot/OldAccessory"  # GameObject を削除（Undo対応、子オブジェクト含む）
prefab-sentinel editor list-children --path "/AvatarRoot" --depth 2  # ランタイム階層の子一覧取得
prefab-sentinel editor list-materials --path "/AvatarRoot/Body"     # ランタイムでレンダラーのマテリアルスロット一覧取得
prefab-sentinel editor list-roots                                  # Scene / Prefab Stage のルートオブジェクト一覧
prefab-sentinel editor camera --yaw 180 --pitch 15               # カメラを背面に回す（pivot はそのまま）
prefab-sentinel editor camera --yaw 90 --pitch 0 --distance 0.5  # 横から近づく
prefab-sentinel editor get-material-property --renderer "/Body/Mesh" --index 0                    # 全シェーダープロパティ一覧
prefab-sentinel editor get-material-property --renderer "/Body/Mesh" --index 0 --property "_Color" # 特定プロパティ値の取得

# Bridge 診断・CI（Unity 環境必須）
prefab-sentinel validate bridge-check                           # Bridge セットアップの診断（環境変数・C# 配置）
prefab-sentinel validate bridge-smoke --plan "config/prefab_patch_plan.json"  # E2E スモークテスト
prefab-sentinel validate smoke-batch --config "config/smoke_config.json"     # avatar/world ターゲットのバッチスモーク
prefab-sentinel validate integration-tests                      # Unity C# 統合テスト実行
prefab-sentinel report smoke-history --input "smoke_results/" --format md    # スモーク履歴の集計・タイムアウトプロファイル
```

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
- **Prefab 編集時**（open モード）: validate structure → dry-run → confirm → validate refs → validate runtime
- **Prefab 新規作成時**（create モード）: patch plan 作成 → dry-run → confirm → validate structure → validate refs
- **計画自動生成 → 適用**: `patch generate circle` → dry-run → confirm（create モードのパイプライン）
- **Unity Editor 起動中の適用**: Editor Bridge セットアップ → `UNITYTOOL_BRIDGE_MODE=editor` → 通常通り `patch apply` / `validate runtime`
- **Editor リモート操作**: `editor select` → `editor frame` で対象を表示 → `editor screenshot` で視覚確認。スクショはトリアージの起点として使い、データソースにしない（必ず `inspect wiring` / `validate refs` で裏取りする）
- **Console ログ取得**: `editor console` でリアルタイムログをテキスト取得 → `--classify` で既存パターンマッチを適用してトリアージ。batchmode 後の Editor.log 読みではなく、Editor 起動中のバッファからの取得
- **フィールド配線検査**: inspect wiring → null参照・fileID不整合の特定、重複は same-component（WARNING）/ cross-component（INFO）に分類。Variant ファイルではベース Prefab のコンポーネントを自動解析
- **階層構造の確認**: inspect hierarchy → ツリー構造・コンポーネント配置の把握。Variant ファイルではベース Prefab の階層を表示し、オーバーライド付きノードに `[overridden: N]` マーカーを付与
- **マテリアル構成の確認**: inspect materials → Renderer ごとのマテリアルスロット一覧。Variant チェーンを考慮し、各スロットが `overridden` / `inherited` かを表示
- **内部構造の検証**: validate structure → fileID重複・Transform整合性・参照欠損の検出
- **壊れた参照の修復**: validate refs → where-used → suggest ignore-guids → safe_fix / decision_required
- **ランタイムエラー調査**: validate runtime → ログ分類 → アセット特定 → 修正提案
- **オーバーライドの削除**: patch revert → Variant の特定の Modification 行を YAML から除去。dry-run → confirm のゲート付き
- **見た目の反復調整**: editor set-material でスロット差し替え → editor camera でアングル調整 → editor screenshot で確認 → 確定後に patch revert / patch apply で永続化
- **Variant チェーン値の追跡**: `inspect variant --show-origin` で各プロパティ値がどの Prefab で設定されたかを表示。3段 Variant (Base → Mid → Leaf) の場合、各値に `origin_path` と `origin_depth` が付与される
- **アクセサリ / 衣装の差し替え**: `editor delete` で旧オブジェクトを削除 → `editor instantiate` で新 Prefab を追加（下記 MA/NDMF セクション参照）
- **ランタイム階層の確認**: `editor list-children` でシーン実行中の子オブジェクト一覧を取得。`inspect hierarchy` はファイルベースだが、こちらは Prefab Instance 内のネスト構造も表示
- **ランタイムマテリアル確認**: `editor list-materials` で Unity API から直接 Renderer のマテリアルスロット一覧を取得。`inspect materials` がオフラインで Variant/FBX チェーンを解決できない場合の代替。各スロットの `material_guid` を `editor set-material --index` に直接使用可能

## VRChat アバター着せ替えワークフロー（MA/NDMF）

MA (Modular Avatar) / NDMF ベースの VRChat アバターでは、コスメティックや衣装の差し替えに Variant チェーン分析は**不要**。以下のパターンで完結する。

### MA/NDMF パターン（着せ替え）
- コスメティック（ネイル、リボン、アクセサリ等）はアバタールートの**子 Prefab として追加するだけ**で、MA がビルド時にマージする
- 差し替え = 旧 Prefab を `editor delete` で削除 + 新 Prefab を `editor instantiate --parent <avatar>` で追加
- Variant チェーンの fileID 比較や m_SourcePrefab 書き換えは**不要**
- 衣装差し替えも同様: 旧衣装 Prefab を削除 → 新衣装 Prefab を子として追加

### 判断フロー
```
着せ替え/差し替え作業？
├─ MA/NDMF ベース → 子オブジェクト操作（instantiate/delete）で完結
│   └─ Variant チェーン分析は不要
└─ 非 MA/NDMF or マテリアル修正 → Variant 分析が有効
    └─ inspect variant / inspect materials で調査
```

### Variant チェーン分析が必要なケース
- マテリアルオーバーライドの調査・修正（`inspect variant` + `inspect materials`）
- プロパティ値の追跡（`--show-origin` でどのレベルの Prefab が設定したか確認）
- Variant 固有の壊れた参照の修復

## 関連スキル（ワークフロー自動化）

本 guide は CLI リファレンス。以下のスキルは複数コマンドを組み合わせたゲート付きワークフローを提供する。

| スキル | 用途 | トリガー |
|--------|------|---------|
| `/prefab-sentinel:variant-safe-edit` | Prefab/Scene/Asset の安全な編集。preflight → dry-run → confirm → validate のフルパイプライン | パッチ適用、Prefab 編集、`patch apply` を使う作業全般 |
| `/prefab-sentinel:prefab-reference-repair` | 壊れた参照の検出・修復。ignore-guid ポリシーと decision_required ゲート付き | `validate refs` で壊れた GUID/fileID が検出されたとき |
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
2. **validate**: structure → wiring → refs

### UdonSharp の二重構造

UdonSharp コンポーネントは 2 つの MonoBehaviour で構成される。片方だけでは "Selected U# behaviour is not setup" エラーになる。

1. **UdonSharpBehaviour** — C# クラスのフィールドを持つ。`_udonSharpBackingUdonBehaviour` で backing を参照
2. **Backing UdonBehaviour** — `m_Script: {guid: 45115577ef41a5b4ca741ed302693907}` 固定。`serializedProgramAsset` と `programSource` を持つ

## 判断ルール
- `safe_fix`: 一意で決定的な修正のみ自動適用可。
- `decision_required`: ユーザー合意まで保留。
- `error` / `critical` が出たら停止し、修正または判断待ちへ回す。
- Unity 環境がない場合は dry-run / 検査までで停止する。
- `patch apply --confirm` は `--change-reason` と `--out-report` を必須とする（監査ログ）。

## Unity ブリッジセットアップ

### アーキテクチャ概要

パッチ実適用とランタイム検証は Unity batchmode を経由する。CLI → `tools/unity_patch_bridge.py`（stdin/stdout JSON プロトコル）→ Unity batchmode → `PrefabSentinel.UnityPatchBridge.ApplyFromJson`。dry-run（`--dry-run`）は Unity 不要だが、`--confirm` による実適用と `validate runtime` は Unity 環境が必須。

### セットアップ手順

1. **C# ブリッジを Unity プロジェクトにコピー**
   ```bash
   # パッチ適用ブリッジ（必須）
   cp tools/unity/PrefabSentinel.UnityPatchBridge.cs <UnityProject>/Assets/Editor/

   # ランタイム検証ブリッジ（validate runtime を使う場合）
   cp tools/unity/PrefabSentinel.UnityRuntimeValidationBridge.cs <UnityProject>/Assets/Editor/

   # Editor 制御ブリッジ（editor screenshot / select / frame 等を使う場合）
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

3. **疎通確認**
   ```bash
   prefab-sentinel validate bridge-smoke --plan "config/prefab_patch_plan.json"
   ```

### Bridge セットアップ手順（インタラクティブ）

ユーザーが Bridge セットアップを依頼した場合:

1. `prefab-sentinel validate bridge-check` を実行し現在の状態を診断する
2. `BC_ENV_*` エラー: ユーザーに Unity プロジェクトパスと Unity 実行ファイルパスを確認する
3. `BC_CS_*` エラー: `${CLAUDE_PLUGIN_ROOT}/tools/unity/` から C# ファイルを `<UnityProject>/Assets/Editor/` にコピーする
4. 環境変数を設定する（ユーザーのシェル設定に追記、または Claude Code settings.json に設定）
5. `prefab-sentinel validate bridge-check` を再実行し全チェック通過を確認する
6. （任意）`prefab-sentinel validate bridge-smoke --plan config/prefab_patch_plan.json` で E2E 疎通を確認する

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
| `UNITYTOOL_PLAN_SIGNING_KEY` | パッチ計画の HMAC 署名キー | — | — |

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
4. 通常通り `patch apply` / `validate runtime` を実行（Unity コマンドの設定は不要）

**アーキテクチャ:** Python → `{uuid}.request.json` をアトミック書き込み → EditorBridge がポーリング検出 → `UnityPatchBridge.ApplyFromPaths` or `UnityRuntimeValidationBridge.RunFromPaths` → `{uuid}.response.json` をアトミック書き込み → Python がポーリング読み取り

### `patch generate` サブコマンド

円形配置などのパッチ計画を自動生成する。

| 引数 | 必須 | デフォルト | 説明 |
|------|------|-----------|------|
| `--output` | Yes | — | 出力 Prefab パス（`Assets/...`） |
| `--count` | Yes | — | 子オブジェクト数 |
| `--radius` | Yes | — | 配置半径 |
| `--root-name` | No | ファイル名から推定 | ルート GameObject 名 |
| `--child-name` | No | `Sphere` | 子オブジェクトのベース名 |
| `--scale` | No | `1.0` | 均一スケール |
| `--axis` | No | `xz` | 回転平面（`xz` / `xy` / `yz`） |
| `--out` | No | stdout | 計画 JSON 出力先 |

### ブリッジコマンド許可リスト

`UNITYTOOL_PATCH_BRIDGE` のコマンドヘッド（先頭トークン）は許可リスト制。許可されているコマンド: `python`, `python3`, `py`, `uv`, `uvx` およびそれらの `.exe` 版、`prefab-sentinel-unity-bridge`, `prefab-sentinel-unity-serialized-object-bridge`。
