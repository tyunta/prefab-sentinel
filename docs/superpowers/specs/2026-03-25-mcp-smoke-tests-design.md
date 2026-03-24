# MCP Auto Smoke Tests — 設計仕様

**日付**: 2026-03-25
**スコープ**: MCP ツールを実 YAML ファイルに対して実行するスモークテスト

---

## 背景

MCP 統合テスト (`report_mcp_integration_test_20260324.md`) では 15 の read-only inspection ツールを手動で検証した。現状のテスト層は以下の構成:

- `test_mcp_server.py` — mock ベース。orchestrator をパッチし、パラメータ受け渡しとレスポンス構造を検証。
- `test_bridge_smoke_samples.py` — Unity Bridge の patch apply をサブプロセスで検証（Unity 必要）。

**ギャップ**: MCP ツールを mock なしで「実際の YAML パース → orchestrator → レスポンス」としてエンドツーエンド実行し、データの正確性まで検証するテストがない。リファクタ後に出力構造が壊れても既存テストでは検出できない。

## 設計方針

- **リグレッション検出が主目的**: 出力構造とデータ正確性の破壊を検知する。実プロジェクトのアセット品質監視は将来拡張。
- **Synthetic YAML をデフォルト**: CI で常に動く静的 fixture ファイル。外部プロジェクトは環境変数指定時のみ。
- **段階的カバレッジ**: コア 5 ツールで基盤を作り、残りに拡張する。
- **既存パターン準拠**: unittest ベース、`_run()` ヘルパーによる async 実行、既存の yaml_helpers 活用。
- **内部参照のみ**: fixture は内部 fileID 参照のみ使用し、外部 GUID 参照を含めない。これにより `.meta` ファイルや GUID index が不要となり、`find_project_root` の成否に依存しない。

## コア 5 ツール

初回スコープでカバーするツール:

1. **`inspect_wiring`** — envelope 形式。`asset_path` でファイル直接指定。
2. **`validate_refs`** — envelope 形式。`asset_path` でファイル直接指定。内部 fileID のみ検査。
3. **`inspect_hierarchy`** — envelope 形式。`asset_path` でファイル直接指定。
4. **`validate_structure`** — envelope 形式。`asset_path` でファイル直接指定。
5. **`get_unity_symbols`** — direct-payload 形式（`symbols` キー）。`asset_path` でファイル直接指定。

`find_referencing_assets` はクロスファイル GUID 検索が必要で `.meta` ファイルなしでは機能しないため、将来拡張に回す。

## 変更内容

### 1. Fixture ファイル — `tests/fixtures/smoke/`

目的別の synthetic YAML ファイルを静的に配置する。fixture の内容変更は期待値変更を伴うため、バージョン管理で明示追跡する。**全 fixture は内部 fileID 参照のみ使用し、外部 GUID 参照（`guid: ...`）はスクリプト参照（`m_Script`）を除き含めない。**

| ファイル | 内容 | 検証対象ツール |
|---------|------|---------------|
| `basic.prefab` | GameObject + MonoBehaviour (valid ref 1 + null ref 1) | inspect_wiring, validate_structure, get_unity_symbols |
| `broken_ref.prefab` | MonoBehaviour のカスタムフィールドが存在しない fileID を参照 | validate_refs, validate_structure |
| `hierarchy.prefab` | 3 階層 (Root > Child > GrandChild) + 正しく配線された Transform (`m_Father`/`m_Children`) | inspect_hierarchy, get_unity_symbols |

**Fixture 構造の具体例**:

`basic.prefab`:
```yaml
# GameObject &100 "BasicObj" + MonoBehaviour &200
# MonoBehaviour fields: validRef: {fileID: 100}, nullRef: {fileID: 0}
# → inspect_wiring: null_ratio="1/2", null_field_names=["nullRef"]
```

`broken_ref.prefab`:
```yaml
# GameObject &100 "BrokenObj" + MonoBehaviour &200
# MonoBehaviour fields: goodRef: {fileID: 100}, badRef: {fileID: 99999}
# → validate_refs: internal_broken_ref_count >= 1 (fileID:99999 not found)
```

`hierarchy.prefab`:
```yaml
# GameObject &100 "Root" + Transform &101 (m_Father: 0, m_Children: [&201])
# GameObject &200 "Child" + Transform &201 (m_Father: &101, m_Children: [&301])
# GameObject &300 "GrandChild" + Transform &301 (m_Father: &201, m_Children: [])
# → inspect_hierarchy: depth=3, root node name="Root"
```

**Fixture 生成方針**: `tests/yaml_helpers.py` の `YAML_HEADER`, `make_gameobject()`, `make_monobehaviour()`, `make_transform()` を使って生成し、静的ファイルとして保存する。

### 2. テストモジュール — `tests/test_mcp_smoke.py`

#### MCP サーバーのセットアップ

`test_mcp_server.py` の既存パターンに従い、`create_server()` でサーバーインスタンスを作成して `call_tool()` を呼ぶ。orchestrator は mock せず、実際の `Phase1Orchestrator` を使う。

`activate_project` は呼ばない。全ツールに `asset_path` パラメータで fixture ファイルの絶対パスを直接渡す。これにより `find_project_root` の成否に依存しない。

```python
from prefab_sentinel.mcp_server import create_server

def _run(coro):
    return asyncio.run(coro)

class McpSmokeTests(unittest.TestCase):
    """Smoke tests that exercise MCP tools against real YAML fixtures (no mocks)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server()
        cls.fixtures_dir = Path(__file__).parent / "fixtures" / "smoke"
```

`call_tool()` は `list[TextContent]` を返す。既存テストパターンに従い `json.loads(result[0].text)` でレスポンス dict を取得する。

#### クラス構成

```python
    # --- inspect_wiring ---
    def test_inspect_wiring_envelope_structure(self) -> None: ...
    def test_inspect_wiring_null_ratio_correct(self) -> None: ...
    def test_inspect_wiring_null_field_names_correct(self) -> None: ...

    # --- validate_refs ---
    def test_validate_refs_detects_broken_ref(self) -> None: ...
    def test_validate_refs_clean_file_passes(self) -> None: ...

    # --- inspect_hierarchy ---
    def test_inspect_hierarchy_returns_three_levels(self) -> None: ...

    # --- validate_structure ---
    def test_validate_structure_detects_issues(self) -> None: ...
    def test_validate_structure_clean_file_passes(self) -> None: ...

    # --- get_unity_symbols ---
    def test_get_unity_symbols_returns_symbols(self) -> None: ...
    def test_get_unity_symbols_includes_game_object_names(self) -> None: ...
```

#### 検証レベル

各テストは以下を検証する:

1. **レスポンス構造**: envelope ツールは `success`, `severity`, `code`, `data`, `diagnostics` キーの存在。direct-payload ツール（`get_unity_symbols`）は `symbols` キーの存在。
2. **データ正確性**: fixture の既知内容に対する具体的な値（例: `null_ratio == "1/2"`, `component_count == 1`）。`null_ratio` の `"N/M"` 文字列形式は `prefab_sentinel/orchestrator.py` の `inspect_wiring` ハンドラで定義されている。
3. **エラー不在**: 想定外の例外やスタックトレースが出ないこと。

### 3. 外部プロジェクトテスト

```python
@unittest.skipUnless(os.environ.get("SMOKE_PROJECT_ROOT"), "no external project")
class McpSmokeExternalTests(unittest.TestCase):
    """Smoke tests against a real Unity project (opt-in via env var)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server()
        cls.project_root = os.environ["SMOKE_PROJECT_ROOT"]

    def test_validate_refs_structure(self) -> None:
        # success=True, data has expected keys — 値は検証しない
        ...

    def test_inspect_wiring_structure(self) -> None:
        # レスポンス構造のみ検証
        ...
```

外部プロジェクトでは **レスポンス構造の正当性のみ** を検証する。fixture 固有の値（null_ratio の具体的数値など）は検証しない。外部テストでは `activate_project` を `setUpClass` で呼び、scope を設定してからツールを実行する。

### 4. CI 統合

- 既存の `scripts/run_unit_tests.py`（unittest-parallel）でそのまま実行される。
- `SMOKE_PROJECT_ROOT` なしでは外部プロジェクトテストが自動スキップされる。
- 追加の CI workflow 設定は不要。

## エッジケース

- **GUID index 空問題**: fixture は内部 fileID 参照のみ使用するため、GUID index が空でも `validate_refs` の内部 fileID 検査には影響しない。`m_Script` の外部 GUID 参照は broken ref として検出されるが、これは既知の挙動として期待値に含める。
- **fixture ファイルの Windows パス**: WSL 環境でもテスト内は Linux パスで完結する。Unity パス変換は不要。
- **将来の read-only ツール拡張**: fixture を追加し、テストメソッドを追加するだけ。テストインフラの変更は不要。`find_referencing_assets` の追加時は `.meta` sidecar ファイルを fixture に含める。

## 影響範囲

| ファイル | 変更内容 |
|---------|---------|
| `tests/test_mcp_smoke.py` | 新規: MCP スモークテストモジュール |
| `tests/fixtures/smoke/basic.prefab` | 新規: valid + null ref の基本 fixture |
| `tests/fixtures/smoke/broken_ref.prefab` | 新規: 壊れた内部参照の fixture |
| `tests/fixtures/smoke/hierarchy.prefab` | 新規: 3 階層 + Transform 配線の fixture |

## やらないこと

- Editor Bridge が必要なツール（`editor_screenshot`, `editor_select` 等）のスモークテスト。
- `set_property` / `patch_apply` 等の書き込みツールのスモークテスト。
- `find_referencing_assets` のスモークテスト（GUID index + `.meta` が必要。将来拡張）。
- pytest への移行や pytest-parametrize の導入。
- `smoke_batch.py` の拡張（設計が subprocess + Unity Bridge 前提のため噛み合わない）。
- fixture の動的生成（内容と期待値の追跡性を優先）。
- `activate_project` による scope 設定（fixture テストでは不要。各ツールに `asset_path` を直接渡す）。

## 検証基準

1. `McpSmokeTests` の全テストが CI で pass すること
2. コア 5 ツール（`inspect_wiring`, `validate_refs`, `inspect_hierarchy`, `validate_structure`, `get_unity_symbols`）がカバーされていること
3. 各ツールについてレスポンス構造とデータ正確性の両方が検証されていること
4. `SMOKE_PROJECT_ROOT` なしで外部プロジェクトテストがスキップされること
5. 既存テスト（1124 件）に影響しないこと
