# 実行リファレンス

MCP サーバー / patch スキーマ / Unity bridge 連携 / 代表レポート出力フォーマットの正本。`README.md` はこのドキュメントへのポインタのみを持つ。

v0.4.0 で CLI (`prefab-sentinel` コマンド) は廃止され、MCP サーバーが唯一のインターフェースとなった。
検査・編集は MCP ツール経由で実行する（ツール一覧は [docs/tools.md](./tools.md) を参照）。

## 実行方法

- MCP サーバー: `prefab-sentinel-mcp`（エントリポイント）。検査・編集はすべて MCP ツール経由で行う（ツール一覧は [docs/tools.md](./tools.md)）。
- 環境変数プレフィックス（`UNITYTOOL_*`）は互換性のため現状維持とする（一覧は [CONFIGURATION.md](../CONFIGURATION.md)）。
- unit test は `scripts/run_unit_tests.py` で並列実行する（「CI / テスト実行」節参照）。

## レポート / ignore-guid

- レポート変換（検査結果 JSON → Markdown / JSON / CSV）は v0.4.0 で廃止された旧 `report export` CLI コマンドの機能だった。MCP ツールとしても公開していない（**Non-Goal**）。変換ロジックは内部関数 `prefab_sentinel.reporting.export_report` に残るが、CLI / MCP いずれの公開インターフェースも持たない。代表的なレポート出力フォーマットはドキュメント末尾の「代表レポート出力フォーマット」節を参照。
- ignore-guid ファイルは UTF-8 テキスト（1 行 1 GUID、`#` 以降コメント可）。`validate_refs` MCP ツールは `<scope>/config/ignore_guids.txt` を auto-load し（存在しなければ無視）、caller 指定の `ignore_asset_guids` 引数と union-dedupe で併用する。詳細は [CONFIGURATION.md](../CONFIGURATION.md) を参照。
- `validate_refs` MCP ツールの `top_missing_asset_guids` を使って無視候補 GUID を特定できる。`top_missing_asset_guids` / `top_ignored_missing_asset_guids` には GUID→アセットパスのベストエフォート解決結果（`asset_name`）が含まれる。

## CI / テスト実行

- `scripts/run_unit_tests.py` は `unittest-parallel` を使って unit test を並列実行する共通入口で、既定では `-s tests -t . -v -j 0` を使う。推奨呼び出しは `uv run --extra test --extra mcp python scripts/run_unit_tests.py`（`--extra mcp` は MCP サーバーをインポートするテストの collection エラー回避に必須。issue #217）。追加引数はそのまま `python -m unittest_parallel` に渡すので、`python scripts/run_unit_tests.py -j 4 -k patch_apply` のように絞り込みや job 数調整もできる。preflight 段階で stale `mutants/` ディレクトリ（exit 3）/ `mcp` extras 不在（exit 4）/ `unittest_parallel` 不在（exit 2）を切り分けて exit する。`mcp` extras 不在の preflight は `PREFAB_SENTINEL_RUN_TESTS_SKIP_MCP_EXTRA` を非空値で export すると bypass できる。
- `.github/workflows/ci.yml` は `lint`（`ruff check` + `mypy`）と `python scripts/run_unit_tests.py` の 2 ジョブのみを自動実行する。Issue #270 で smoke / benchmark / live-Unity / batchmode 統合テスト workflow（旧 `unity-smoke.yml` / `unity-live-nightly.yml` / `unity-integration.yml`）は削除済みで、対応する Python の smoke / benchmark / 統合テスト関連モジュールも issue #289 で削除済み。

## Patch / attestation
- `patch apply` は plan JSON のスキーマ検証と `dry_run_patch` プレビューを実装済み（open mode: prefab `set` / `insert_array_element` / `remove_array_element`、material / ScriptableObject root asset mutation、scene `open_scene` / hierarchy / component / `save_scene`、prefab create mode: `create_prefab` / `create_root` / `create_game_object` / `rename_object` / `reparent` / `add_component` / `find_component` / `remove_component` / `save`、material / ScriptableObject create mode: `create_asset` / `save` + mutation op、scene create mode: `create_scene` / `create_game_object` / `instantiate_prefab` / `rename_object` / `reparent` / `add_component` / `find_component` / `remove_component` / `save_scene`）。
- prefab create mode の mutation op（`set` / `insert_array_element` / `remove_array_element`）は `component` selector ではなく、create mode 中に確保した component `$handle` を `target` に指定して適用する。
- material / ScriptableObject の open mode mutation は root asset `$asset` を `target` に指定し、create mode では `create_asset` が返す asset `$handle` を `target` に指定して適用する。
- scene mode は予約済み `$scene` handle を root parent として使い、hierarchy op の `parent` に指定する。scene 内 mutation op は `add_component` / `find_component` が返す component `$handle` を `target` に指定して適用する。
- `patch apply` は `--out-report` 指定時に結果 envelope を JSON ファイルに保存する（`--confirm` 時は必須）。
- `patch apply` は非 dry-run 時に `--confirm` と `--change-reason` を要求し、JSON ターゲット（`.json`）は内蔵バックエンドで実編集する。
- `patch_apply` MCP ツールは attestation ファイルから期待値（sha256 / signature）を読み取って適用前照合できる。
- `patch_apply` MCP ツールは scope 指定時に `scan_broken_references` を事前実行し、`error` / `critical` で fail-fast 停止する。
- `patch_apply` MCP ツールは `.prefab` ターゲットで `list_overrides` を事前実行し、`error` / `critical` で fail-fast 停止する。
- `patch_apply` MCP ツールは Unity ターゲット（`.prefab` / `.unity` / `.asset` など）に対して `UNITYTOOL_PATCH_BRIDGE` 経由の外部 bridge を使って適用できる。
- `UNITYTOOL_PATCH_BRIDGE` は JSON 入力（stdin） / JSON 出力（stdout）の bridge コマンドを指定する（外部 bridge request `protocol_version: 2`）。

## `patch_apply` 入力スキーマ（annotated examples）

`patch_apply` の `plan` パラメータに渡す JSON のスキーマ。`plan_version: 2` が唯一受け入れられる形状。`plan_version` を欠くペイロード（旧 `{"target": ..., "ops": [...]}` 形状を含む）は `normalize_patch_plan` が `ValueError` を送出して即時拒否する。外部 `unity_patch_bridge` は、トップレベルに `target` キーを含むリクエストを `BRIDGE_LEGACY_SCHEMA_REJECTED`（`severity="error"`, exit code `1`）で拒否する。互換レイヤや `target` → `resources[0].path` の自動補正は存在しない。

### スキーマ概要

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "<resource-id>", "kind": "<kind>", "path": "<asset-path>", "mode": "<mode>"}
  ],
  "ops": [
    {"resource": "<resource-id>", "op": "<op-type>", ...}
  ],
  "postconditions": [
    {"type": "<check-type>", ...}
  ]
}
```

| フィールド | 必須 | 説明 |
|---|---|---|
| `plan_version` | ✅ | `2` 固定 |
| `resources` | ✅ | 操作対象アセットの一覧。最低 1 件 |
| `resources[].id` | ✅ | ops から参照する識別子（任意の文字列） |
| `resources[].path` | ✅ | Unity アセットパス（`Assets/...`） |
| `resources[].kind` | — | `prefab` / `scene` / `material` / `asset` / `json`（省略時はパス拡張子から推定） |
| `resources[].mode` | — | `open`（既存編集）/ `create`（新規作成）。既定 `open` |
| `ops` | ✅ | 操作配列（順序実行） |
| `ops[].resource` | ✅ | `resources[].id` への参照 |
| `ops[].op` | ✅ | 操作種別（下記参照） |
| `postconditions` | — | 適用後の検証条件（省略可） |

### op 種別一覧

**open mode（既存アセット編集）:**

| op | 必須フィールド | 説明 |
|---|---|---|
| `set` | `component`, `path`, `value` | コンポーネントのプロパティ値を設定 |
| `insert_array_element` | `component`, `path`, `index`, `value` | 配列に要素を挿入 |
| `remove_array_element` | `component`, `path`, `index` | 配列から要素を削除 |

- Prefab の `component` はクラス名（例: `"PlayerScript"`, `"UnityEngine.MeshRenderer"`）または階層修飾 selector `TypeName@/hierarchy/path`（例: `"MeshRenderer@/Body/Head"`）。offline write ツール（`set_property` / `set_properties`）は issue #37 以降、解決済み component の GameObject 祖先チェーンから後者を発行する（同型コンポーネントが複数あるアセットでも一意 component を指せる）
- Material / ScriptableObject の open mode では `component` の代わりに `"target": "$asset"` でルートを指定

**create mode（新規アセット作成）:**

| op | 必須フィールド | 説明 |
|---|---|---|
| `create_prefab` / `create_asset` / `create_scene` | — | アセットを新規作成 |
| `create_root` | `name` | Prefab ルート GameObject 作成 |
| `create_game_object` | `name`, `parent` | 子 GameObject 作成 |
| `instantiate_prefab` | `prefab`, `parent` | Prefab をシーンにインスタンス化 |
| `rename_object` | `target`, `name` | GameObject リネーム |
| `reparent` | `target`, `parent` | 親変更 |
| `add_component` | `target`, `type` | コンポーネント追加（`result` で `$handle` を返す） |
| `find_component` | `target`, `type` | 既存コンポーネント取得（`result` で `$handle` を返す） |
| `remove_component` | `target` | コンポーネント削除（`target` はコンポーネント `$handle`） |
| `set` | `target`, `path`, `value` | `$handle` 経由でプロパティ設定 |
| `save` / `save_scene` | — | ディスクに保存 |

### 例 1: Prefab のプロパティ編集（open mode）

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "avatar", "path": "Assets/Avatars/MyAvatar.prefab"}
  ],
  "ops": [
    {
      "resource": "avatar",
      "op": "set",
      "component": "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor",
      "path": "lipSync",
      "value": 5
    }
  ]
}
```

### 例 2: Prefab の配列操作（open mode）

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "stage", "path": "Assets/Prefabs/Stage.prefab"}
  ],
  "ops": [
    {
      "resource": "stage",
      "op": "set",
      "component": "StageLighting",
      "path": "lights.Array.size",
      "value": 3
    },
    {
      "resource": "stage",
      "op": "insert_array_element",
      "component": "StageLighting",
      "path": "lights.Array.data",
      "index": 2,
      "value": {"guid": "aabb00112233445566778899aabbccdd", "file_id": "100100000"}
    }
  ],
  "postconditions": [
    {"type": "asset_exists", "resource": "stage"},
    {"type": "broken_refs", "scope": "Assets/Prefabs", "expected_count": 0}
  ]
}
```

### 例 3: Material / ScriptableObject の編集（open mode）

Material と ScriptableObject は root asset mutation を使い、`"target": "$asset"` で対象を指定する:

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "mat", "kind": "material", "path": "Assets/Materials/Hair.mat"}
  ],
  "ops": [
    {
      "resource": "mat",
      "op": "set",
      "target": "$asset",
      "path": "m_Shader",
      "value": {"guid": "aabb00112233445566778899aabbccdd", "file_id": "4800000"}
    }
  ]
}
```

> **Note:** Material の個別プロパティ編集（`_Color`, `_MainTex` 等）には `set_material_property`（YAML 直接編集）または `editor_set_material_property`（Editor Bridge 経由）を使う方が簡単。`patch_apply` は Material の `m_Shader` やシリアライズ済みフィールドへの直接書き込みに使う。

### 例 4: Scene のオブジェクト操作（create mode）

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "scene", "kind": "scene", "path": "Assets/Scenes/Main.unity", "mode": "create"}
  ],
  "ops": [
    {"resource": "scene", "op": "create_scene"},
    {
      "resource": "scene",
      "op": "create_game_object",
      "name": "SpawnPoint",
      "parent": "$scene"
    },
    {
      "resource": "scene",
      "op": "instantiate_prefab",
      "prefab": "Assets/Prefabs/Stage.prefab",
      "parent": "$scene"
    },
    {"resource": "scene", "op": "save_scene"}
  ]
}
```

### 例 5: 複数リソースの一括操作

```json
{
  "plan_version": 2,
  "resources": [
    {"id": "base", "path": "Assets/Prefabs/Base.prefab"},
    {"id": "variant", "path": "Assets/Prefabs/Variant.prefab"}
  ],
  "ops": [
    {
      "resource": "base",
      "op": "set",
      "component": "AudioSource",
      "path": "m_Volume",
      "value": 0.8
    },
    {
      "resource": "variant",
      "op": "set",
      "component": "AudioSource",
      "path": "m_Pitch",
      "value": 1.2
    }
  ]
}
```

### postconditions 種別

| type | フィールド | 説明 |
|---|---|---|
| `asset_exists` | `resource` | 指定リソースのアセットファイルが存在するか検証 |
| `broken_refs` | `scope`, `expected_count` | スコープ内の壊れた参照数が期待値と一致するか検証 |

## `patch apply --confirm --out-report` の出力例

before / after diff + validation steps の抜粋:
```json
{
  "success": true,
  "severity": "info",
  "code": "PATCH_APPLY_RESULT",
  "message": "patch.apply completed.",
  "data": {
    "execution_id": "8f0c2b7c0e8f4f30a3d3a7f0f1f1e2aa",
    "executed_at_utc": "2026-02-17T00:00:00+00:00",
    "change_reason": "apply prefab patch",
    "steps": [
      {
        "step": "dry_run_patch",
        "result": {
          "code": "SER_DRY_RUN_OK",
          "data": {
            "diff": [
              {
                "op": "set",
                "path": "nested.value",
                "before": "(unknown)",
                "after": 42
              }
            ]
          }
        }
      },
      {
        "step": "apply_and_save",
        "result": {
          "code": "SER_APPLY_OK",
          "data": {
            "diff": [
              {
                "op": "set",
                "path": "nested.value",
                "before": 10,
                "after": 42
              }
            ]
          }
        }
      },
      {
        "step": "assert_no_critical_errors",
        "result": {
          "code": "RUN_ASSERT_OK"
        }
      }
    ]
  }
}
```

## `set_properties` パラメータ

issue #41 で `set_component_fields` から改名。`symbol_path` は GameObject ではなくコンポーネントを直接指す（独立した `component` 引数は廃止）。

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `asset_path` | string | ✅ | — | アセットファイルパス（.prefab, .unity, .asset） |
| `symbol_path` | string | ✅ | — | 対象コンポーネントの人間可読パス（例: `"Controller/DualButtonController"`, `"Body/Head/MonoBehaviour(PlayerScript)"`） |
| `properties` | dict | ✅ | — | プロパティパス → 新しい値のマッピング（`{"propertyPath": value, ...}`） |
| `dry_run` | bool | — | `false` | `true` で変更を書き込まずプレビューする（`confirm=true` と同時に指定した場合は `dry_run` が優先） |
| `confirm` | bool | — | `false` | `true` で変更を適用（`change_reason` と `out_report` が必須） |
| `change_reason` | string | — | `null` | 変更理由（監査証跡用）。`confirm=true` 時は必須 |
| `out_report` | string | — | `null` | 結果 JSON を書き出すファイルパス。`confirm=true` 時は必須 |

**未解決時の挙動**: `symbol_path` がコンポーネントに解決できない場合は `SYMBOL_NOT_FOUND` / `SYMBOL_AMBIGUOUS` / `SYMBOL_NOT_COMPONENT` を返す。dry-run 段階で `properties` 内の property path がチェーン上に見つからない場合、`SER003`（severity=`error`）の error envelope を返す。`data.suggestions` に近似候補（最大 5 件）、`diagnostics[].detail` に `property_not_found` を載せる（issue #109）。発行する patch op は階層修飾 selector `TypeName@/hierarchy/path` を用いる（issue #37）。祖先名に `#` を含む等で selector が表現不能なら `SELECTOR_NOT_EXPRESSIBLE` で fail-fast。

**使用例（dry-run）:**

```json
{
  "asset_path": "Assets/Prefabs/Controller.prefab",
  "symbol_path": "Controller/DualButtonController",
  "properties": {
    "clearDelaySeconds": 60.0,
    "buttonA": {"guid": "aabbccdd11223344aabbccdd11223344", "fileID": 12345, "type": 2}
  },
  "dry_run": true
}
```

**使用例（confirm）:**

```json
{
  "asset_path": "Assets/Prefabs/Controller.prefab",
  "symbol_path": "Controller/DualButtonController",
  "properties": {"clearDelaySeconds": 60.0},
  "confirm": true,
  "change_reason": "タイマー値を 30s から 60s に変更",
  "out_report": "reports/set_fields_result.json"
}
```

## `editor_set_properties` パラメータ

issue #41 で `editor_set_component_fields` から改名。

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `hierarchy_path` | string | ✅ | — | 対象 GameObject の Hierarchy パス（例: `"/DualButtonController/Controller"`） |
| `component_type` | string | ✅ | — | コンポーネント型名（例: `"DualButtonController"`） |
| `properties` | list[dict] | ✅ | — | プロパティエントリのリスト（各要素は `property_name` + `value` または `property_name` + `object_reference`） |

**`properties` エントリ形式:**

| 形式 | フィールド | 説明 |
|------|-----------|------|
| プリミティブ値 | `{"property_name": "speed", "value": "60"}` | 数値・文字列・bool を文字列として渡す |
| オブジェクト参照 | `{"property_name": "areaCollider", "object_reference": "/DualButtonController/AreaCollider:BoxCollider"}` | Hierarchy パス + オプションコンポーネント型 |

各エントリは bridge 境界を越えて `value_present` マーカー（bool）を運び、空文字列 `value` と `value` 不在を区別する（issue #52）。

**使用例:**

```json
{
  "hierarchy_path": "/DualButtonController/Controller",
  "component_type": "DualButtonController",
  "properties": [
    {"property_name": "clearDelaySeconds", "value": "60"},
    {"property_name": "areaCollider", "object_reference": "/DualButtonController/AreaCollider:BoxCollider"}
  ]
}
```

すべてのプロパティ変更は単一 Undo グループにまとめられる。

## Unity bridge / runtime

> 環境変数 / ignore_guids.txt / scope config の正本は [CONFIGURATION.md](../CONFIGURATION.md) を参照。

- `tools/unity_patch_bridge.py` は常駐 Editor Bridge に JSON リクエスト / レスポンスファイルを介してパッチ計画を中継する（mutation op の `value` を Unity 側で扱える型情報へ正規化し、prefab create mode の hierarchy / component op、material / ScriptableObject create mode の `create_asset`、scene open/create mode の hierarchy / prefab instantiate / component op、および `save` / `save_scene` を中継する）。
- `tools/unity_patch_bridge.py` の外部 request は `plan_version: 2` + `resources[]` + `ops[]` を受け付け、resource ごとに分解して bridge へ渡す。
- `tools/unity_patch_bridge.py` の mutation op は prefab open mode では `component`、material / ScriptableObject open mode では root asset `$asset` の `target`、scene mode では component `$handle` の `target`、create mode では component / asset `$handle` の `target` を受け付ける。
- `tools/unity_patch_bridge.py` は bridge 送信前に `ops` を検証し、`set` の `value` 欠落や配列操作の `index` 欠落などを `BRIDGE_REQUEST_SCHEMA` で fail-fast 停止する。
- `tools/unity_patch_bridge.py` は `UNITYTOOL_BRIDGE_WATCH_DIR` で Editor Bridge との接続先 watch ディレクトリを指定する。`UNITYTOOL_UNITY_TIMEOUT_SEC` でポーリング上限を、`UNITYTOOL_UNITY_PROJECT_PATH` / `UNITYTOOL_UNITY_LOG_FILE` で実行設定を制御できる。`UNITYTOOL_BRIDGE_WATCH_DIR` 未設定時は `BRIDGE_WATCH_DIR_MISSING` で fail-fast 停止する。
- WSL 環境対応: `prefab_sentinel/wsl_compat.py` が WSL 検出・パス変換（`wslpath` 経由）・スペース入りパスの復元を提供し、`unity_patch_bridge.py` と `runtime_validation/` から利用する。`UNITYTOOL_UNITY_PROJECT_PATH` / `UNITYTOOL_BRIDGE_WATCH_DIR` は Windows パス（`D:/...`）でも WSL パス（`/mnt/d/...`）でも受け付ける。`wslpath` 不在時はグレースフル・デグレードする。
- `tools/unity_patch_bridge.py` は Editor Bridge 応答の `success/severity/code/message/data/diagnostics` を厳密検証し、欠落・型不一致時は `BRIDGE_UNITY_RESPONSE_SCHEMA` で fail-fast 停止する。
- `prefab_sentinel/services/serialized_object/` の resource dispatch は `json` / `prefab` / `asset` / `material` / `scene` の adapter ごとに分離し、Unity 側に渡す resource plan は常に kind / mode を明示した bridge request へ正規化する。
- `prefab_sentinel/services/serialized_object/` は 1 ファイル 300 行以内の責務別モジュール構成に分割されている（`service.py` が facade、`patch_dispatch` / `patch_preview` / `patch_validator` / `patch_executor` / `patch_json_apply` が JSON ターゲット flow、`resource_bridge` / `resource_bridge_invoke` が Unity Editor bridge 構成、`resource_plan` / `resource_adapters` が resource scope 入り口、`asset_open_ops` / `asset_create_ops` / `asset_create_writers` / `scene_dispatch` / `scene_object_ops` / `scene_component_ops` / `scene_values` / `prefab_create_dispatch` / `prefab_create_structure` / `prefab_create_values` が open/create mode バリデータ群）。公開 API は `prefab_sentinel.services.serialized_object.SerializedObjectService` のみで、後方互換のための re-export やシムは置かない。
- `tools/unity/PrefabSentinel.UnityPatchBridge.cs` は Editor Bridge から呼び出される実装として `.prefab` の open mode `set` / `insert_array_element` / `remove_array_element`、`.mat` / `.asset` の open mode root asset mutation、`.unity` の open/create mode `open_scene` / `create_scene` / hierarchy / `instantiate_prefab` / component op / `save_scene`、および create mode の prefab root / hierarchy / component op、material / ScriptableObject の `create_asset`、`$handle` 参照 mutation、`save` を適用する（prefab mutation 時の `component` は一意一致必須、component 曖昧時は候補パス付きで fail-fast）。
- `prefab_sentinel/services/runtime_validation/` は `UNITYTOOL_BRIDGE_WATCH_DIR` が指す Editor Bridge 監視ディレクトリへ `compile_udonsharp` / `run_clientsim` リクエストを JSON で書き出し、`{uuid}.response.json` を待ち受ける。watch ディレクトリ未設定時は `RUN_CONFIG_ERROR` 応答で fail-fast し、未配線を明示する。
- `tools/unity/PrefabSentinel.UnityRuntimeValidationBridge.cs` は runtime validation 用の Editor 内実装で、UdonSharp compile と ClientSim 起動を行い、`success/severity/code/message/data/diagnostics` 形式の応答を返す。エントリーポイントは file-IPC 用 `RunFromPaths(requestPath, responsePath)` のみ。
- **Editor Bridge セットアップ**: Unity Editor で `PrefabSentinel > Editor Bridge` メニューから EditorWindow を開き、watch ディレクトリを指定する。Python 側は `UNITYTOOL_BRIDGE_WATCH_DIR` に `{uuid}.request.json` を書き込み、`{uuid}.response.json` の出現をポーリングする。Editor Bridge は `EditorApplication.update` で 500 ms 間隔ポーリングし、`action` フィールドで patch / runtime を自動判別する。アトミック書き込み（`.tmp` → rename）で読み取り競合を防止する。
- `component` セレクタは `TypeName@Hierarchy/Path` 形式を受け付け、同型コンポーネントが複数ある場合に GameObject 階層で明示的に絞り込める。
- `set` の値デコードは `int/float/bool/string/null` に加えて `Character` / `LayerMask` / `ArraySize`、`enum`、`Color`、`Vector2/3/4`、`Vector2Int/3Int`、`Rect/RectInt`、`Bounds/BoundsInt`、`Quaternion`、`AnimationCurve`、`Gradient`、`ObjectReference` / `ExposedReference`（`value_kind=json` の `{guid,file_id}` または `{guid,fileID}`）、`ManagedReference`（`value_kind=json`、必要時 `{"__type":"Namespace.Type, Assembly"}` ヒント対応）、`Generic`（カスタム構造体の `value_json` 反映）を扱う。
- `ObjectReference` は Unity 組み込みリソース（`Library/unity default resources`、`Resources/unity_builtin_extra`）を解決できる。組み込みパス検出時は (1) `Library/unity default resources` と `Resources/unity_builtin_extra` の両パスに対して `AssetDatabase.LoadAllAssetsAtPath` で GUID+fileID マッチング、(2) 既知組み込みアセット名テーブルから `AssetDatabase.GetBuiltinExtraResource` / `Resources.GetBuiltinResource` で直接ロード+GUID+fileID 検証（Editor Bridge コンテキストで `LoadAllAssetsAtPath` が空を返す遅延ロード問題への対策）、(3) `Resources.FindObjectsOfTypeAll` 最終フォールバック の三段階で解決する。通常の `LoadMainAssetAtPath` パスはバイパスする。JSON キーは `fileID`（Unity ネイティブ形式、`plan_generators` 出力）と `file_id`（snake_case、example plan 互換）の両方を受け付ける。
- `AnimationCurve` は `value_kind=json` で `{ "keys":[{"time":0.0,"value":1.0,"in_tangent":0.0,"out_tangent":0.0}], "pre_wrap_mode":1, "post_wrap_mode":1 }` 形式を受け付ける（`value_kind=null` で null 設定）。
- `Gradient` は `value_kind=json` で `{ "color_keys":[{"color":{"r":1,"g":1,"b":1,"a":1},"time":0.0}], "alpha_keys":[{"alpha":1.0,"time":0.0}], "mode":0 }` 形式を受け付ける（`value_kind=null` で null 設定）。
- 配列操作パスの診断は `.Array.data` 形式を厳密検証し、`.Array.size` / index 付き誤指定時はヒント付きで停止する。
- fixed buffer 配列に対する `insert_array_element` / `remove_array_element` は未対応として明示的に fail-fast 停止し、要素更新は `set` で個別要素パスを指定する方針とする。
- patch plan v2 は任意の `postconditions` 配列を受け付け、`patch apply` 完了前に検証する。現状の対応型は `asset_exists`（`resource` または `path`）と `broken_refs`（`scope`, `expected_count`, `exclude_patterns`, `ignore_asset_guids`）で、不一致時は fail-fast で停止する。
- `validate_runtime` MCP ツールは `inspect_world_canvas` / `compile_udonsharp` / `run_clientsim` / `collect_unity_console` / `classify_errors` / `assert_no_critical_errors` を順に実行する。`inspect_world_canvas` は scene YAML を直接読む静的 leading step（issue #121）で、WorldSpace Canvas + `VRC_UiShape` の `localScale` 不整合を `WORLD_CANVAS_LOCAL_SCALE`（warning）、BoxCollider 欠落を `WORLD_CANVAS_MISSING_BOX_COLLIDER`（info）として surface する。step の severity は warning に capping されており、後続の Editor Bridge steps は必ず実行する。Editor Bridge が `UNITYTOOL_BRIDGE_WATCH_DIR` で配線されていれば UdonSharp compile と ClientSim を実行し、未配線時は `RUN_CONFIG_ERROR` で fail-fast して未配線を明示する。VRChat persistent listener の許可リストや UI イベント配線パターンは `../knowledge/vrchat-event-binding.md`、WorldSpace Canvas のハードルールは `../knowledge/vrchat-sdk-worlds.md` を参照（issue #120 / #121）。
- `collect_unity_console(runtime_root, log_file, ...)` は `log_file` を `runtime_root` 配下に封じ込める（どちらも symlink 解決後の絶対パスに正規化し、`runtime_root` 外を指す入力は `RUN_CONFIG_ERROR` で fail-fast）。ログファイルの読み取りは `UnicodeDecodeError` を吸収せず、復号失敗時は `RUN_LOG_DECODE_WARN`（warning severity の success 応答、`log_lines=[]`）を返し、後段の分類器で empty classification 扱いとする。
- `PrefabVariantService.resolve_chain_values(variant_path, diagnostics=None)` および下層の `resolve_chain_values` module 関数は、復号失敗（`OSError` / `UnicodeDecodeError`）を沈黙で `{}` に丸めない。呼び出し側が `diagnostics: list[Diagnostic]` を渡した場合、該当ファイルについて `detail="unreadable_file"` の診断を末尾に追記する（診断 sink 未指定時も従来どおり `{}` を返すが、`revert_overrides` は sink を渡して応答の `diagnostics[]` に伝搬する）。

## read-only 検査ツール詳細

- `inspect_variant` は Prefab chain / overrides / stale 候補（重複 override・`Array.size` 不整合）を返す。
- `find_referencing_assets` は GUID / asset の参照元を scope 指定で検索し、`max_usages` 超過分を `truncated_usages` に集計する。
- `validate_refs` は `missing_asset` / `missing_local_id` を検出する。
- Unity 組み込み GUID（例: `0000000000000000e000000000000000` / `f000...`）は欠落判定から除外する。
- GUID インデックスは scope が属する Unity プロジェクトルート（最寄り `Assets` 親）で構築し、`Library` / `Logs` / `Temp` / `obj` など既定除外ディレクトリは走査しない。
- `validate_refs` の結果には `scan_project_root`（GUID インデックスに使った Unity プロジェクトルート）を含む。
- 外部 `*.prefab` 参照の fileID 検証は誤検知回避のため既定でスキップし、件数を `skipped_external_prefab_fileid_checks` に集計する。
- `validate_refs` の `categories` はユニーク問題件数（例: missing GUID 単位）を返し、発生回数は `categories_occurrences` / `broken_occurrences` で確認する。
- ノイズ判定に使えるよう、`top_missing_asset_guids` に missing GUID 上位を返す。
- `ignore_asset_guids` パラメータで missing GUID を一時的に無視でき、集計は `ignored_missing_asset_occurrences` / `top_ignored_missing_asset_guids` で確認できる。
- `find_referencing_assets` も同じ既定除外を適用し、`Library` など非本番スコープを走査しない。
- 書き込み操作は `patch_apply`（confirm モード）、`set_property`、`set_properties`、`copy_component_fields`、`add_component`、`remove_component`、`revert_overrides` の各 MCP ツールで利用可能。

## エラーヒント ("Did you mean...?")

- `SYMBOL_NOT_FOUND` エラー（`set_property`, `set_properties`, `copy_component_fields`, `add_component`, `remove_component`）は `data.suggestions` に類似 symbol_path のリスト（最大 3 件）を含む。
- `MAT_PROP_NOT_FOUND` エラー（`inspect_material_asset` の書き込みモード）は `data.suggestions` に類似プロパティ名のリスト（最大 3 件）を含む。既存の `data.available_properties`（全プロパティ名リスト）も維持される。
- `EDITOR_CTRL_PROPERTY_NOT_FOUND` エラー（`editor_get_material_property`, `editor_set_material_property`）は `data.suggestions` に類似シェーダープロパティ名のリスト（最大 3 件）を含む。
- 候補なしの場合は `suggestions` は空配列 `[]`。
- Python 側は `difflib.SequenceMatcher`、C# 側は Levenshtein 距離を使用（アルゴリズム差異あり、結果の完全一致は保証しない）。

## VRC SDK アップロード

## マルチプラットフォームアップロード

- `vrcsdk_upload` の `platforms` パラメータで複数プラットフォームへの順次ビルド+アップロードが可能。
- 有効値: `"windows"`, `"android"`, `"ios"`。デフォルト: `["windows"]`。
- 順次実行し、途中失敗で残りをスキップする。完了後は元のビルドターゲットに復元する。
- レスポンスの `data.platform_results` に per-platform の結果（成功/失敗/スキップ）を含む。
- `data.original_target_restored` で元のビルドターゲットの復元成否を確認できる。
- 複数プラットフォーム時は `timeout_sec` を `600 * len(platforms)` 程度に設定することを推奨。

## 代表レポート出力フォーマット

```md
# Prefab Sentinel Validation Report
- RunId: 20260211-235959-abc123
- Scope: Assets/MyProject
- Result: FAILED

### Findings
1. REF002 Missing local fileID
   - Location: MyGroup Variant.prefab / target_list.Array.data[0]
   - Evidence: fileID 6858960407220450596 not found
   - Suggested Fix: map to existing VRCPickup fileID 87704510201466299

2. RUN001 Udon runtime exception
   - Location: GameController.cs:200
   - Evidence: audio_sources[i] null
   - Suggested Fix: ignore invalid entries or set audio_sources size=0
```
