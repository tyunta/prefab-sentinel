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
- **段階的カバレッジ**: コア 5 ツールで基盤を作り、残り 10 に拡張する。
- **既存パターン準拠**: unittest ベース、`_run()` ヘルパーによる async 実行、既存の yaml_helpers 活用。

## 変更内容

### 1. Fixture ファイル — `tests/fixtures/smoke/`

目的別の synthetic YAML ファイルを静的に配置する。fixture の内容変更は期待値変更を伴うため、バージョン管理で明示追跡する。

| ファイル | 内容 | 検証対象ツール |
|---------|------|---------------|
| `basic.prefab` | GameObject + MonoBehaviour (valid ref 1 + null ref 1) | inspect_wiring, validate_structure, get_unity_symbols |
| `broken_ref.prefab` | MonoBehaviour の fileID が存在しない内部参照 | validate_refs, validate_structure |
| `hierarchy.prefab` | 3 階層 (Root > Child > GrandChild) + 各 Transform | inspect_hierarchy, find_referencing_assets |
| `multi_component.prefab` | 複数 MonoBehaviour + 相互参照 + null ref 混在 | inspect_wiring, find_referencing_assets |

**Fixture 生成方針**: `tests/yaml_helpers.py` の `YAML_HEADER`, `make_gameobject()`, `make_monobehaviour()`, `make_transform()` を使って生成し、静的ファイルとして保存する。ヘルパーで動的生成しない理由は、fixture の内容がテストの期待値と 1:1 対応するため、変更を明示的に追跡する必要があるため。

### 2. テストモジュール — `tests/test_mcp_smoke.py`

#### クラス構成

```python
class McpSmokeTests(unittest.TestCase):
    """Smoke tests that exercise MCP tools against real YAML fixtures (no mocks)."""

    @classmethod
    def setUpClass(cls) -> None:
        # MCP server instance を作成
        # activate_project 相当の scope 設定（fixtures dir）
        ...

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

    # --- find_referencing_assets ---
    def test_find_referencing_assets_returns_matches(self) -> None: ...
```

#### 検証レベル

各テストは以下を検証する:

1. **レスポンス構造**: envelope ツールは `success`, `severity`, `code`, `data`, `diagnostics` キーの存在。direct-payload ツールは `matches` / `symbols` キーの存在。
2. **データ正確性**: fixture の既知内容に対する具体的な値（例: `null_ratio == "1/2"`, `component_count == 1`）。
3. **エラー不在**: 想定外の例外やスタックトレースが出ないこと。

#### MCP サーバーのセットアップ

`test_mcp_server.py` の既存パターンに従い、`PrefabSentinelMcpServer` を直接インスタンス化して `call_tool()` を呼ぶ。ただし orchestrator は mock せず、実際の `Phase1Orchestrator` を使う。

```python
@classmethod
def setUpClass(cls) -> None:
    cls.server = PrefabSentinelMcpServer()
    cls.fixtures_dir = Path(__file__).parent / "fixtures" / "smoke"
```

`activate_project` が必要なツール（`validate_refs`, `find_referencing_assets`）は、`setUpClass` で fixtures ディレクトリを scope として activate する。不要なツール（`inspect_wiring`, `inspect_hierarchy`, `validate_structure`）は `asset_path` パラメータで直接ファイルを指定する。

#### Async 実行

既存の `_run()` パターンを使用:

```python
def _run(coro):
    return asyncio.run(coro)
```

### 3. 外部プロジェクトテスト

```python
@unittest.skipUnless(os.environ.get("SMOKE_PROJECT_ROOT"), "no external project")
class McpSmokeExternalTests(unittest.TestCase):
    """Smoke tests against a real Unity project (opt-in via env var)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = PrefabSentinelMcpServer()
        cls.project_root = os.environ["SMOKE_PROJECT_ROOT"]
        # activate_project with project_root

    def test_validate_refs_structure(self) -> None:
        # success=True, data has expected keys — 値は検証しない
        ...

    def test_inspect_wiring_structure(self) -> None:
        # レスポンス構造のみ検証
        ...
```

外部プロジェクトでは **レスポンス構造の正当性のみ** を検証する。fixture 固有の値（null_ratio の具体的数値など）は検証しない。

### 4. CI 統合

- 既存の `scripts/run_unit_tests.py`（unittest-parallel）でそのまま実行される。
- `SMOKE_PROJECT_ROOT` なしでは外部プロジェクトテストが自動スキップされる。
- 追加の CI workflow 設定は不要。

## エッジケース

- **activate_project 失敗**: fixtures dir はプロジェクトルートではないため、`find_project_root` が失敗する。`inspect_wiring` 等の asset_path 直接指定ツールは影響なし。`validate_refs` / `find_referencing_assets` は scope パラメータで fixtures dir を指定し、project root 不要のパスで動作させる。
- **fixture ファイルの Windows パス**: WSL 環境でもテスト内は Linux パスで完結する。Unity パス変換は不要。
- **将来の read-only 10 ツール拡張**: fixture を追加し、テストメソッドを追加するだけ。テストインフラの変更は不要。

## 影響範囲

| ファイル | 変更内容 |
|---------|---------|
| `tests/test_mcp_smoke.py` | 新規: MCP スモークテストモジュール |
| `tests/fixtures/smoke/basic.prefab` | 新規: valid + null ref の基本 fixture |
| `tests/fixtures/smoke/broken_ref.prefab` | 新規: 壊れた内部参照の fixture |
| `tests/fixtures/smoke/hierarchy.prefab` | 新規: 3 階層の fixture |
| `tests/fixtures/smoke/multi_component.prefab` | 新規: 複数コンポーネントの fixture |

## やらないこと

- Editor Bridge が必要なツール（`editor_screenshot`, `editor_select` 等）のスモークテスト。
- `set_property` / `patch_apply` 等の書き込みツールのスモークテスト。
- pytest への移行や pytest-parametrize の導入。
- `smoke_batch.py` の拡張（設計が subprocess + Unity Bridge 前提のため噛み合わない）。
- fixture の動的生成（内容と期待値の追跡性を優先）。

## 検証基準

1. `McpSmokeTests` の全テストが CI で pass すること
2. コア 5 ツール（`inspect_wiring`, `validate_refs`, `inspect_hierarchy`, `validate_structure`, `find_referencing_assets`）がカバーされていること
3. 各ツールについてレスポンス構造とデータ正確性の両方が検証されていること
4. `SMOKE_PROJECT_ROOT` なしで外部プロジェクトテストがスキップされること
5. 既存テスト（1124 件）に影響しないこと
