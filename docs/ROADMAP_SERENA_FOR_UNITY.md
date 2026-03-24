# Roadmap: Prefab Sentinel → "Serena for Unity"

## 1. ビジョン

Prefab Sentinel の最終目標は **Unity 版 Serena** — AI エージェントが Unity のアセット構造を、ソースコードと同じレベルで意味的に理解・操作できるツール。

Serena がソースコードに対して提供する価値:
- シンボルモデルによる構造理解
- セマンティックナビゲーション（名前で辿れる）
- セマンティック編集（シンボル単位の安全な書き換え）
- MCP サーバーとして任意の AI エージェントから呼べる

これを Unity アセット（Prefab / Scene / Material / UdonBehaviour）に対して実現する。

---

## 2. 現状の強み

Prefab Sentinel は「安全な編集パイプライン」として高い完成度を持つ。これは Serena にはない独自価値。

| 能力 | 実装状況 |
|------|----------|
| 参照検証 (`validate refs`) | 完成 — GUID/fileID の実体照合、壊れた参照の検出 |
| Variant override 検査 (`inspect variant`) | 完成 — Base→Variant チェーンの差分可視化 |
| 安全な編集 (`patch apply`) | 完成 — dry-run → confirm のゲート付きワークフロー |
| 実行時検証 (`runtime-validation`) | 完成 — Udon/ClientSim ログのエラー分類 |
| 階層検査 (`inspect hierarchy`) | 完成 — GameObject ツリーの構築・表示 |
| Udon 配線検査 (`inspect wiring`) | 完成 — MonoBehaviour フィールドの列挙 |
| 構造化レスポンス (`ToolResponse`) | 完成 — success/severity/code/message/data/diagnostics |

**一言で言えば: 「目」と「手」はある。「名前」が無い。**

---

## 3. Serena の核心的価値（ベンチマーク）

| 価値 | 説明 |
|------|------|
| **シンボルモデル** | AST ベースで class/method/property の階層構造を理解 |
| **セマンティックナビゲーション** | `find_symbol`, `find_referencing_symbols` で意味的に辿れる |
| **セマンティック編集** | `replace_symbol_body` でシンボル単位の安全な書き換え |
| **MCP サーバー** | 任意の AI エージェントがツールとして呼べる |
| **プロジェクトスコープ** | `activate_project` でスコープを絞り、効率的に操作 |
| **ステートフルセッション** | メモリ、モード切替、オンボーディングで文脈を保持 |
| **インクリメンタル探索** | overview → symbol → body → references の段階的深掘り |

---

## 4. ギャップ分析

### Gap 1: シンボルモデルが無い — 最大のギャップ

**Serena**: `ClassName/method_name` で「コード上の何か」を一意に指せる。
**Prefab Sentinel**: fileID + ClassID + propertyPath の3つ組でしかオブジェクトを指せない。

**具体例**:
- Serena: 「`PlayerController/OnJump` を見せて」→ メソッド本体が返る
- Prefab Sentinel: 「`CharacterBody` の `MeshRenderer` の `m_Materials[0]` を見せて」→ **言えない**。fileID を知っている必要がある

**必要なもの**: Unity アセットの「シンボル」抽象
```
Scene
  └ GameObject "CharacterBody"        ← 名前で指せるべき
      ├ Transform                     ← コンポーネント型で指せるべき
      ├ MeshRenderer
      │   └ m_Materials[0]            ← プロパティパスで指せるべき
      └ UdonBehaviour "PlayerScript"  ← スクリプト名で指せるべき
          └ moveSpeed                 ← シリアライズフィールドで指せるべき
```

**既に持っているパーツ**:
- `hierarchy.py`: GameObject ツリーは構築できる
- `udon_wiring.py`: MonoBehaviour フィールドは列挙できる
- `unity_yaml_parser.py`: YAML ブロックをパースできる
- **足りないのは、これらを統合した「名前 → fileID」解決レイヤー**

---

### Gap 2: MCP サーバーインターフェースが無い

**Serena**: MCP プロトコルで任意の AI エージェント（Claude, Cursor, etc.）から呼べる。
**Prefab Sentinel**: CLI + Claude Code プラグイン（Skills）のみ。

**ギャップ**:
- CLI は人間向け。AI エージェントには構造化された入出力が必要
- Skills は Claude Code 専用。他のエージェントからは使えない
- `ToolResponse` は既に構造化されているので、MCP ラッパーは薄くできるはず

**既に持っているパーツ**:
- `contracts.py` の `ToolResponse` は MCP tool response にほぼ直接マッピングできる
- `Phase1Orchestrator` のメソッドがそのまま MCP tool になりうる
- `.claude-plugin/` は既にプラグイン構造

---

### Gap 3: インクリメンタル探索ができない

**Serena**: overview → `find_symbol(depth=1)` → `find_symbol(include_body=True)` → `find_referencing_symbols` と段階的に深掘り。
**Prefab Sentinel**: `inspect variant` は全情報を一度に返す。「もう少し詳しく」が無い。

**具体例**:
- Serena: 「Foo クラスの概要」→「jump メソッドの本体」→「jump を呼んでいる箇所」
- Prefab Sentinel: 「Variant の全オーバーライド一覧」のみ。「この Transform の子だけ見せて」「このコンポーネントのフィールドだけ見せて」ができない

**必要なもの**:
- `get_symbols_overview(path, depth=0)` 相当 — ルートの GameObject 一覧
- `find_symbol("CharacterBody/MeshRenderer", include_body=True)` 相当 — 特定コンポーネントのプロパティ
- `find_referencing_symbols("script_guid")` 相当 — このスクリプトを使っている全 Prefab

---

### Gap 4: セマンティック編集の抽象度が低い

**Serena**: `replace_symbol_body("Foo/jump", new_code)` — シンボル名で編集。
**Prefab Sentinel**: JSON patch plan を手動構築 → `patch apply`。

**ギャップ**:
- 「CharacterBody の moveSpeed を 5.0 にして」を実行するには、fileID を調べ、patch plan JSON を書き、apply する必要がある
- Serena 的には `set_property("CharacterBody/PlayerScript/moveSpeed", "5.0")` と言いたい

**既に持っているパーツ**:
- `serialized_object.py` の `dry_run_patch` / `apply_and_save` は高機能
- **足りないのは「名前ベースのアドレッシング → fileID/propertyPath への解決」**
- シンボルモデル (Gap 1) が解決すれば、この層は薄い変換になる

---

### Gap 5: C# ソースとの接続が無い

**Serena**: C# AST を理解できる（パーサーがあれば）。
**Prefab Sentinel**: YAML に書かれたシリアライズ結果のみ理解。

**具体例**:
- C# で `public float moveSpeed;` を `public float runSpeed;` にリネーム
- Prefab の propertyPath `moveSpeed` が壊れる
- Prefab Sentinel はこの因果関係を知らない

**必要なもの**: C# field 定義 ↔ propertyPath のマッピング
- 完全な AST は不要。`[SerializeField]` と public field の列挙だけでよい
- UdonSharp スクリプトの public/SerializeField を列挙し、propertyPath との対応表を構築
- フィールドリネーム時の影響範囲を検出

---

### Gap 6: ステートフルセッションが無い

**Serena**: `activate_project` → 以後のクエリはそのスコープ内。
**Prefab Sentinel**: 毎回 `--scope` / `--path` を渡す。GUID インデックスも毎回再構築。

**ギャップ**:
- GUID インデックス構築は重い（大規模プロジェクトで数秒）
- 同じプロジェクトで連続操作するのにキャッシュが無い
- MCP サーバー化すればプロセス常駐でキャッシュが効く

---

## 5. 到達度マトリクス

| Serena の価値 | 現状 | 到達度 | 主要ギャップ |
|---------------|------|--------|-------------|
| シンボルモデル | fileID/ClassID/propertyPath | 30% | 名前ベースアドレッシング |
| セマンティックナビ | inspect/validate 系コマンド | 50% | インクリメンタル深掘り |
| セマンティック編集 | patch plan + apply | 60% | 名前 → fileID 解決 |
| MCP サーバー | CLI + Claude Code plugin | 20% | MCP プロトコル実装 |
| プロジェクトスコープ | `--scope` パラメータ | 40% | activate/session 管理 |
| ステートフル | 完全ステートレス | 10% | プロセス常駐 + キャッシュ |
| C# 接続 | YAML のみ | 0% | field ↔ propertyPath マッピング |

---

## 6. フェーズ計画

### P1: シンボルモデル + MCP サーバー

**一緒にやるべき理由**: シンボルモデルは MCP tool のインターフェースを規定し、MCP サーバーはシンボルモデルの利用形態を規定する。並行設計が効率的。

#### P1-A. シンボルパス形式

Unity アセット内のオブジェクトを人間可読な名前で指すための正規パス形式:

```
symbol_path := segment ( "/" segment )*
segment     := name | name "#" index | name "[" index "]"
               | "MonoBehaviour(" script_name_or_guid ")"
```

**例:**
| パス | 意味 |
|------|------|
| `CharacterBody` | GameObject |
| `CharacterBody/MeshRenderer` | コンポーネント（ClassID 名） |
| `CharacterBody/MonoBehaviour(PlayerScript)` | スクリプト名で特定した MonoBehaviour |
| `CharacterBody/MonoBehaviour(PlayerScript)/moveSpeed` | シリアライズフィールド |
| `CharacterBody/MeshRenderer/m_Materials[0]` | 配列要素 |

**曖昧性解消ルール:**
- 同名の兄弟 GO → `Cube`, `Cube#1`, `Cube#2`（最初は接尾辞なし）
- 名前なし GO → `<unnamed:fileID>`
- GO に MonoBehaviour が1つ → `MonoBehaviour` 単体で可（lenient resolve）
- 組み込みコンポーネント → ClassID 名（GO に同型は1つ）

#### P1-B. データ構造

**新規モジュール:** `prefab_sentinel/symbol_tree.py`

```python
class SymbolKind(StrEnum):
    GAME_OBJECT = "game_object"
    COMPONENT = "component"
    PROPERTY = "property"

@dataclass(slots=True)
class SymbolNode:
    kind: SymbolKind
    name: str               # セグメント名
    file_id: str            # Unity fileID（property は空文字列）
    class_id: str           # Unity ClassID
    children: list[SymbolNode]
    script_guid: str        # MonoBehaviour のみ
    script_name: str        # .cs.meta から解決
    depth: int
    properties: dict[str, str]  # field_name → raw_value

@dataclass(slots=True)
class SymbolTree:
    asset_path: str
    roots: list[SymbolNode]
    _file_id_index: dict[str, SymbolNode]
    _path_cache: dict[str, list[SymbolNode]]
```

**公開 API:**
```python
SymbolTree.build(text, asset_path, guid_to_script_name=None) -> SymbolTree
SymbolTree.resolve(symbol_path) -> list[SymbolNode]
SymbolTree.resolve_unique(symbol_path) -> SymbolNode  # 0件/2件+ で raise
SymbolTree.query(symbol_path, depth=0, include_properties=False) -> list[SymbolNode]
SymbolTree.to_overview(depth=0) -> list[dict]

build_script_name_map(project_root: Path) -> dict[str, str]
```

**構築フロー（既存パーサー統合）:**
1. `split_yaml_blocks()` → 全ブロック
2. `parse_game_objects()` → GO 名 + コンポーネント fileID リスト
3. `parse_transforms()` → 親子関係
4. `parse_components()` → ClassID + script_guid
5. `hierarchy.CLASS_NAMES` → コンポーネント型名
6. `guid_to_script_name` → MonoBehaviour スクリプト名
7. `_parse_monobehaviour_fields()` → フィールド値（property レベルクエリ時のみ）

#### P1-C. MCP サーバー

**ライブラリ:** `mcp` 公式 Python SDK — optional dependency

```toml
[project.optional-dependencies]
mcp = ["mcp>=1.12"]
```

**新規モジュール:** `prefab_sentinel/mcp_server.py`
**CLI エントリ:** `prefab-sentinel serve [--transport stdio|streamable-http] [--project-root PATH]`

MCP 未インストール時は明確なエラーで停止。CLI の zero-dependency は維持。

#### P1-D. MCP ツール定義（初期6ツール）

| ツール | 説明 | マップ先 |
|--------|------|----------|
| `get_unity_symbols` | アセットのシンボルツリー取得（depth 指定可） | `SymbolTree.build()` + `to_overview()` |
| `find_unity_symbol` | シンボルパスで検索 | `SymbolTree.resolve()` + `query()` |
| `find_referencing_assets` | GUID/パスの参照元検索 | `orchestrator.inspect_where_used()` |
| `validate_refs` | 壊れた参照のスキャン | `orchestrator.validate_refs()` |
| `inspect_wiring` | MonoBehaviour 配線分析 | `orchestrator.inspect_wiring()` |
| `inspect_variant` | Variant override チェーン分析 | `orchestrator.inspect_variant()` |

**出力マッピング:** `ToolResponse.to_dict()` をそのまま MCP ツール結果として返す。ドメインエラーは `success: false` + `diagnostics` で表現し、MCP レベル `isError` はインフラ障害にのみ使用。

#### P1-E. ファイルレイアウト

**新規ファイル:**
- `prefab_sentinel/symbol_tree.py` — シンボルモデル
- `prefab_sentinel/mcp_server.py` — MCP サーバー
- `tests/test_symbol_tree.py` — シンボルモデルテスト
- `tests/test_mcp_server.py` — MCP サーバーテスト

**変更ファイル:**
- `prefab_sentinel/hierarchy.py` — `_CLASS_NAMES` → `CLASS_NAMES`（公開化）
- `prefab_sentinel/cli.py` — `serve` サブコマンド追加
- `pyproject.toml` — `mcp` optional dependency 追加

**依存関係:**
```
unity_yaml_parser ← 変更なし
       ↓
  hierarchy ← CLASS_NAMES 公開化のみ
       ↓
  udon_wiring ← 変更なし
       ↓
  symbol_tree ← 新規（上記3 + unity_assets に依存）
       ↓
  mcp_server ← 新規（symbol_tree + orchestrator + mcp SDK）
       ↓
  cli ← serve サブコマンド追加
```

#### P1-F. 実装順序（3PR）

**PR #1: シンボルモデル（MCP 依存なし）**
1. `hierarchy.py`: `_CLASS_NAMES` → `CLASS_NAMES`
2. `symbol_tree.py`: データ構造 + `SymbolTree.build()` + パス解決 + クエリ
3. `build_script_name_map()`: .cs.meta からスクリプト名解決
4. `test_symbol_tree.py`: 包括テスト（空アセット、ネスト階層、重複名、MonoBehaviour 解決、プロパティ、パス解決）

完了基準: 合成 YAML から `SymbolTree.build()` → `resolve("CharacterBody/MeshRenderer")` → fileID 返却。

**PR #2: MCP サーバー**
1. `pyproject.toml`: optional dependency
2. `mcp_server.py`: FastMCP + 6ツール
3. `cli.py`: `serve` サブコマンド
4. `test_mcp_server.py`: ツール登録・呼び出しテスト

完了基準: `prefab-sentinel serve --transport stdio` → MCP ツール一覧/呼び出しに応答。

**PR #3: 統合仕上げ**
1. `README.md` に MCP サーバー使用方法を追加
2. E2E 検証: 実 .prefab で `get_unity_symbols` → 人間可読シンボルツリー返却

---

### P2: インクリメンタル探索 ✅ 実装済み

**前提:** P1 の `SymbolTree` が `depth` パラメータと `include_properties` をサポートしている。P2 はその上に Variant 対応と使用パターン最適化を追加する。

**実装結果:**
- `diff_unity_symbols` MCP ツール: Variant と Base の差分のみ返す（`orchestrator.diff_variant()` に委譲）
- `find_unity_symbol` の `show_origin` パラメータ: プロパティに Variant チェーンのオリジン情報を注釈
- オリジン追跡は SymbolTree に入れず MCP ツール層でマージ（設計判断: SymbolTree は単一 YAML のみ知る）

**depth セマンティクス（P1 で実装済みの予定）:**
- `depth=0`: ルート GameObject 一覧
- `depth=1`: GO + コンポーネント一覧
- `depth=2`: コンポーネント + プロパティ一覧

**P2 で追加するもの:**

1. **Variant override 注釈**: `find_unity_symbol` で Variant アセットを指定した場合、各プロパティに「この値は Base から？ Variant から？ どの depth の Variant から？」を表示
   - 既存の `PrefabVariantService.resolve_chain_values_with_origin()` を統合
   - `SymbolNode` に `origin: str` フィールド追加（`default=""`、P1 の SymbolNode を後方互換に拡張）

2. **差分クエリ**: `diff_unity_symbols(variant_path)` MCP ツール — Variant と Base の差分のみ返す
   - 既存の `detect_stale_overrides()` + `list_overrides()` を統合
   - AI エージェントが「何が変わっているか」だけを効率的に取得

3. **AI エージェントの使用パターン**:
```
get_unity_symbols("Player.prefab", depth=0)
  → ["CharacterBody", "WeaponHolder", "Canvas"]

find_unity_symbol("CharacterBody", depth=1)
  → ["Transform", "MeshRenderer", "MonoBehaviour(PlayerScript)"]

find_unity_symbol("CharacterBody/MonoBehaviour(PlayerScript)", include_properties=True)
  → {moveSpeed: {value: "5.0", origin: "variant@depth=1"},
     jumpForce: {value: "10.0", origin: "base"}}

diff_unity_symbols("PlayerVariant.prefab")
  → [{path: "CharacterBody/MonoBehaviour(PlayerScript)/moveSpeed",
      base: "3.0", variant: "5.0"}]
```

---

### P3: セマンティック編集の名前解決 ✅ 部分実装済み

**目標:** シンボルパスで値を設定できる。P1 のシンボルモデルがあれば変換レイヤーは薄い。

**実装方針:**
1. `SymbolTree.resolve_unique()` でシンボルパス → SymbolNode を解決
2. SymbolNode のコンポーネントタイプ名 + propertyPath から V2 patch plan を自動生成
3. 既存の dry-run → confirm ワークフローに統合（`Phase1Orchestrator.patch_apply()`）

**実装済み MCP ツール:**

| ツール | パラメータ | 動作 |
|--------|-----------|------|
| `set_property` | `path`, `symbol_path`, `property_path`, `value`, `confirm`, `change_reason` | シンボル → タイプ名解決 → patch plan → dry-run/apply |

**未実装（P3.5 — bridge プロトコル拡張待ち）:**

| ツール | ブロッカー |
|--------|-----------|
| `add_component` | `add_component` は create-mode 専用 op。既存アセットへの open-mode 操作には bridge に `find_game_object` が必要 |
| `remove_component` | `remove_component` は create-mode 専用 op。同上 |

**使用例:**
```
set_property("Player.prefab", "CharacterBody/MonoBehaviour(PlayerScript)", "moveSpeed", 5.0)
  → 内部:
    1. resolve_unique("CharacterBody/MonoBehaviour(PlayerScript)") → SymbolNode
    2. _resolve_component_name(node) → "PlayerScript"
    3. patch plan: {op: "set", component: "PlayerScript", path: "moveSpeed", value: 5.0}
    4. confirm=False → dry_run_patch() → before/after diff
    5. confirm=True → apply_and_save() → 書き込み
```

**実装ノート:**
- シンボル解決エラー（見つからない / 曖昧 / コンポーネントでない / スクリプト名未解決）は `success: false` + エラーコードで返す
- レスポンスに `symbol_resolution` メタデータ（解決されたコンポーネント名、fileID、class_id）を付与
- MonoBehaviour は `script_name`（C# クラス名）で識別。`--project-root` がないと解決不可
- 同一 GO 上の同型コンポーネント重複は bridge 側の `TypeName@/hierarchy/path` 解決に依存（dry-run で検出可能）

**制約:** `confirm` ゲートは維持。MCP ツール経由でも dry-run 結果を返し、明示的な `confirm=True` を要求する。

---

### P4: C# ↔ propertyPath 接続

**目標:** C# フィールド定義と Unity シリアライズの対応を理解する。

**スコープ:** 完全な C# AST パーサーは不要。正規表現ベースで以下を抽出:
- `public` フィールド（型名 + フィールド名）
- `[SerializeField]` 属性付き private フィールド
- `[NonSerialized]` 属性（除外用）
- `[Header]`, `[Tooltip]` 属性（メタデータ用、optional）

**新規モジュール:** `prefab_sentinel/csharp_fields.py`

```python
@dataclass(slots=True)
class CSharpField:
    name: str           # フィールド名
    type_name: str      # 型名 (float, GameObject, etc.)
    is_serialized: bool # SerializeField or public
    line: int

def parse_serialized_fields(source: str) -> list[CSharpField]
def build_field_map(project_root: Path) -> dict[str, list[CSharpField]]
    # script_guid → fields
```

**MCP ツール:**

| ツール | 動作 |
|--------|------|
| `validate_field_rename` | 旧名→新名のリネームで影響を受ける Prefab/Scene を検出 |
| `list_serialized_fields` | スクリプト GUID or パスのシリアライズフィールド一覧 |
| `check_field_coverage` | C# フィールドと Prefab propertyPath の突合（未使用検出） |

**統合:** P1 の `SymbolTree` で MonoBehaviour のプロパティを表示する際、C# 側の型情報を注釈として付加できる。

---

### P5: ステートフルセッション

**P1 の MCP サーバー化で自然に解決する部分が大きい。**
MCP サーバーはプロセス常駐なので、リクエスト間でメモリ上にキャッシュを保持できる。

**キャッシュ対象と無効化戦略:**

| キャッシュ | 構築コスト | 無効化トリガー |
|-----------|-----------|---------------|
| GUID インデックス | 大規模で数秒 | .meta ファイルの追加/削除 |
| SymbolTree | アセットサイズ依存 | 対象 .prefab/.unity の変更 |
| スクリプト名マップ | .cs.meta 全スキャン | .cs.meta の追加/削除 |

**実装方針:**
1. `watchfiles` (pure-Python file watcher) で Assets/ を監視
2. 変更検知時に該当キャッシュのみ無効化（全再構築ではない）
3. `activate_project(scope)` MCP ツールでスコープを固定し、監視対象を限定

**新規 MCP ツール:**
- `activate_project(scope)` — スコープ設定 + 初期キャッシュ構築
- `get_project_status()` — キャッシュ状態、最終更新、監視中ファイル数

**依存:** `watchfiles` を optional dependency に追加（P5 専用）

---

## 7. 依存関係

```
P1 (シンボルモデル + MCP)
 ├── P2 (インクリメンタル探索) ← P1 の depth 拡張
 ├── P3 (セマンティック編集) ← P1 の名前解決を利用
 └── P5 (ステートフル) ← P1 の MCP サーバーを常駐化

P4 (C# 接続) ← 独立して着手可能だが、P1 と統合すると価値が最大化
```

---

## 8. 既存ロードマップとの関係

`docs/IDEAS_AND_ROADMAP.md` は Phase 1〜4（参照検証・編集安定化・実行検証・運用高度化）の実装タスクを管理しており、全て完了済み。

本ドキュメントはその**次のステージ** — CLI ツールから MCP ベースのセマンティックプラットフォームへの進化を計画する。
