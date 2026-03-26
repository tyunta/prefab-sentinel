# Editor Bridge & Inspect 改善設計

## Goal

Editor Bridge のスクリーンショットワークフロー改善、Nested Prefab シンボルツリー展開、Scene ハンドルのバグ修正、inspect materials の Variant 空問題解消の 4 件を一括で対処する。

## スコープ

### A. editor_screenshot 前 refresh

`editor_screenshot` に `refresh: bool = True` パラメータを追加。True（デフォルト）なら撮影前に `refresh_asset_database` アクションを先行実行する。

Python 側で `send_action` を 2 回呼ぶ。C# 側の変更なし。

デフォルト True の根拠: refresh 忘れによるスクショ不整合が実運用で頻発するため。不要な場合のみ `refresh=False` で回避する。

### B. Nested Prefab シンボルツリー展開

`SymbolTree.build()` に `expand_nested: bool = False` と `guid_to_asset_path: dict[str, Path] | None = None` パラメータを追加。

#### PrefabInstance の検出

Unity YAML で PrefabInstance はクラス ID `1001` のブロックとして出現する。`unity_yaml_parser.py` に `CLASS_ID_PREFAB_INSTANCE = "1001"` 定数を追加し、`split_yaml_blocks()` が返すブロック群から class_id で識別する。

PrefabInstance ブロックの `m_SourcePrefab` フィールドから参照先 GUID を抽出する:

```yaml
--- !u!1001 &12345
PrefabInstance:
  m_SourcePrefab: {fileID: 100100000, guid: abc123..., type: 3}
```

既存の `SOURCE_PREFAB_PATTERN`（`unity_assets.py`）を流用してブロックテキストから GUID を取得する。

#### SymbolKind と SymbolNode の拡張

```python
class SymbolKind(StrEnum):
    GAME_OBJECT = "game_object"
    COMPONENT = "component"
    PROPERTY = "property"
    PREFAB_INSTANCE = "prefab_instance"  # 新規追加
```

`SymbolNode` に `source_prefab: str = ""` フィールドを追加（dataclass field、デフォルト空文字列）。

`to_dict()` のシリアライズ:

```python
if self.source_prefab:
    result["source_prefab"] = self.source_prefab
```

`source_prefab` は `PREFAB_INSTANCE` ノードにのみ設定される。値は `guid_to_asset_path` で解決したアセット相対パス（例: `"Assets/Clothing/Shirt.prefab"`）。解決できない場合は GUID 文字列をそのまま格納する。

#### 展開ルール

1. `expand_nested=True` のとき、`build()` 内でクラス ID `1001` のブロックを走査する
2. ブロックテキストから `m_SourcePrefab` の GUID を `SOURCE_PREFAB_PATTERN` で抽出する
3. `guid_to_asset_path` で GUID → `Path` 解決。ファイルを読み込み `SymbolTree.build()` を再帰呼び出し（`expand_nested=True` 引き継ぎ）
4. 子 Prefab のルートノード群を PrefabInstance マーカーノードの `children` として接続する
5. 再帰深度上限: 10（`_MAX_NESTED_DEPTH` 定数。Unity 公式の Nested Prefab 上限に合わせる）
6. 解決できない場合（GUID 不在、ファイル不在、読み取り失敗）は `[Unresolved: {guid}]` マーカーノード（`kind=PREFAB_INSTANCE`, `source_prefab=guid`, 子なし）で打ち切り

#### パス解決における PrefabInstance ノードの扱い

`resolve()` での PrefabInstance ノードは透過的コンテナとして機能する。`_segment_matches()` は PrefabInstance ノード自体にはマッチしないが、`_resolve_segments()` で PrefabInstance ノードの `children` も候補に含めて探索する。

つまり `Avatar/Shirt/SkinnedMeshRenderer` のようなパスで、`Shirt` が PrefabInstance 内の子ノードであっても解決できる。PrefabInstance ノード自体を名前で指定したい場合は `[PrefabInstance: ...]` 表記をサポートしない（現時点では不要）。

#### ツリー表現

```
Avatar (GameObject)
  Body (GameObject)
    SkinnedMeshRenderer
  [PrefabInstance: Assets/Clothing/Shirt.prefab]
    Shirt (GameObject)
      SkinnedMeshRenderer
    Armature (GameObject)
```

マーカーノードは `kind: "prefab_instance"` で `source_prefab` フィールドにアセットパスを持つ。`name` は `"[PrefabInstance: {asset_path}]"`、`file_id` は PrefabInstance ブロックの fileID、`class_id` は `"1001"`。

#### GUID マップ

`guid_to_asset_path` は `collect_project_guid_index()` の返却値（`dict[str, Path]`）をそのまま使用する。orchestrator 経由では既存の GUID インデックスを流用する。`build()` の直接呼び出しで `guid_to_asset_path=None` なら展開スキップ。

#### MCP ツール

`get_unity_symbols` に `expand_nested: bool = False` パラメータ追加。デフォルト False で後方互換を維持する。`expand_nested=True` の場合、orchestrator が `collect_project_guid_index()` を呼んで `guid_to_asset_path` を `build()` に渡す。

### C. Scene $scene ハンドル修正

#### 問題

`patch_apply` の scene open モードで `find_component` が `"target": "$scene"` を使うと、`$scene` の kind が `"scene"` だが `_validate_scene_add_component_op` 内の `_require_handle_ref` が `expected_kind="game_object"` を要求するためミスマッチエラーになる。

#### 修正

`_validate_scene_add_component_op` 内で `find_component` と `add_component` を区別する:

- **`find_component`**: `expected_kind={"scene", "game_object"}` に変更。`$scene` ハンドル（kind `"scene"`）をルート GO の検索コンテキストとして許可し、scene YAML 内の全ブロックを検索対象にする。
- **`add_component`**: `expected_kind="game_object"` を維持。Scene 自体にコンポーネントを追加する操作は意味をなさないため、引き続き拒否する。

具体的には `_validate_scene_add_component_op` の `_require_handle_ref` 呼び出し（現在 `serialized_object.py:2262`）で:

```python
expected_kind = {"scene", "game_object"} if op_name == "find_component" else "game_object"
object_handle = self._require_handle_ref(
    ...,
    expected_kind=expected_kind,
)
```

`_require_handle_ref()` 自体は変更不要（既に `set[str]` を受け付ける）。

prefab_create モードの同等メソッド `_validate_pcreate_add_component_op` は変更不要。Prefab には `$scene` ハンドルが存在しないため影響しない。

### D. inspect materials Variant 空問題

#### 問題

Variant チェーンを辿っても renderer ブロックが見つからず `renderer_count: 0` になる。主因は Nested Prefab の子 Prefab 内に renderer があるケース。現在の `_inspect_variant_materials` は Variant チェーンを `m_SourcePrefab` で遡るが、base Prefab 内の PrefabInstance（クラス ID 1001）の先にある子 Prefab のファイルまでは読みに行かない。

#### 修正

`_inspect_variant_materials` の既存フローの後に Nested Prefab フォールバックを追加する:

1. Variant チェーンウォーキング（既存）で base Prefab を解決した後、renderer が空 **かつ** `_build_stripped_renderer_materials` でも空の場合にのみ Nested 展開を試みる
2. base Prefab テキストからクラス ID `1001` ブロックを抽出し、`m_SourcePrefab` の GUID で子 Prefab ファイルを特定する
3. 子 Prefab を読み込み `_inspect_base_materials` で renderer を収集する（再帰なし、1 階層のみ）
4. 収集した renderer の `RendererMaterials` に `source_prefab: str` フィールドを追加し、どの Nested Prefab 由来かを明示する
5. Nested 展開でも見つからない場合、`MaterialInspectionResult` に `diagnostics: list[str]` フィールドを追加し、理由を出力して空結果を返す（現状は無言で空）

#### RendererMaterials の拡張

```python
@dataclass(slots=True)
class RendererMaterials:
    game_object_name: str
    renderer_type: str
    file_id: str
    slots: list[MaterialSlot]
    source_prefab: str = ""  # 新規追加: Nested Prefab 由来の場合のみ設定
```

#### MaterialInspectionResult の拡張

```python
@dataclass(slots=True)
class MaterialInspectionResult:
    target_path: str
    is_variant: bool
    base_prefab_path: str | None
    renderers: list[RendererMaterials]
    diagnostics: list[str] = field(default_factory=list)  # 新規追加
```

#### 出力例

```json
{
  "renderers": [
    {
      "path": "Body",
      "type": "SkinnedMeshRenderer",
      "source_prefab": "Assets/Models/Body.prefab",
      "materials": [...]
    }
  ]
}
```

#### SymbolTree との関係

D の Nested Prefab renderer 収集は `material_inspector.py` 内で完結する。B の `SymbolTree.build(expand_nested=True)` は使用しない。理由: material_inspector は YAML ブロックレベルで renderer を直接探索する方が効率的であり、SymbolTree を経由するとパフォーマンスオーバーヘッドが不要に増える。

共有するのは PrefabInstance 検出ロジック（クラス ID `1001` 定数）と GUID 解決（`collect_project_guid_index()`、既に material_inspector が使用中）のみ。

## Out of Scope

- `editor_screenshot` の C# 側変更（Python 側で完結するため不要）
- inspect wiring Variant 対応（調査の結果、既に `is_overridden` フラグが実装済みで動作していた）
- patch_apply での BlendShape 名前指定シンタックスシュガー（`editor_set_blend_shape` で代替可能）

## 変更対象ファイル

| ファイル | 変更内容 |
|---------|---------|
| `prefab_sentinel/mcp_server.py` | `editor_screenshot` に `refresh` パラメータ、`get_unity_symbols` に `expand_nested` パラメータ |
| `prefab_sentinel/unity_yaml_parser.py` | `CLASS_ID_PREFAB_INSTANCE = "1001"` 定数追加 |
| `prefab_sentinel/symbol_tree.py` | `SymbolKind.PREFAB_INSTANCE` 追加、`SymbolNode.source_prefab` フィールド追加、`build()` に `expand_nested` + `guid_to_asset_path` + 再帰展開ロジック、`_resolve_segments` で PrefabInstance 透過探索 |
| `prefab_sentinel/services/serialized_object.py` | `_validate_scene_add_component_op` で `find_component` 時のみ `$scene` ハンドルを許可 |
| `prefab_sentinel/material_inspector.py` | `RendererMaterials.source_prefab` 追加、`MaterialInspectionResult.diagnostics` 追加、Nested Prefab 展開による renderer 収集 |
| `prefab_sentinel/orchestrator.py` | `get_unity_symbols` に `guid_to_asset_path` 受け渡し |
| `tests/test_mcp_server.py` | screenshot delegation テスト更新 |
| `tests/test_symbol_tree.py` | Nested Prefab 展開テスト追加 |
| `tests/test_services.py` | $scene ハンドル修正テスト追加 |
| `tests/test_material_inspector.py` | Variant 空問題 regression テスト追加 |

## テスト方針

- **A**: delegation テストで `refresh=True` 時に `send_action` が 2 回呼ばれることを検証。`refresh=False` 時は 1 回のみ。
- **B**: 合成 YAML で PrefabInstance → 子 Prefab ファイルの展開を検証。再帰深度上限テスト。GUID 不在時の `[Unresolved]` マーカー打ち切りテスト。ファイル読み取り失敗時の打ち切りテスト。`to_dict()` で `source_prefab` が含まれることを検証。パス解決で PrefabInstance ノードを透過的に横断できることを検証。
- **C**: scene kind ハンドルで `find_component` が通ることを検証。`add_component` で `$scene` ターゲットが拒否されることを検証。
- **D**: Variant チェーンで renderer 空 → Nested 展開で renderer 発見のシナリオテスト。Nested 展開でも renderer が見つからない場合に `diagnostics` に理由が含まれることを検証。`source_prefab` フィールドが正しく付与されることを検証。
