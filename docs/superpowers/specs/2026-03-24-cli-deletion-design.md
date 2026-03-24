# CLI 完全削除

Phase 2 で MCP ツール 36 個が揃い、AI エージェントの全操作が MCP で完結するようになった。CLI (`prefab-sentinel` コマンド) を削除し、コードベースを MCP 中心に整理する。

## Background

- Phase 1+2 で CLI の inspect/validate/patch/editor 系機能は全て MCP に移植済み。
- CLI 固有の機能（`suggest ignore-guids`, `report export`, `report smoke-history`, `validate bridge-check`）は AI ワークフローに不要。
- CI ワークフローが CLI コマンドを使用しているため、代替呼び出しへの書き換えが必要。

## Scope

### 削除対象

| ファイル | 行数 | 理由 |
|---------|------|------|
| `prefab_sentinel/cli.py` | 2,087 | CLI 本体 |
| `prefab_sentinel/__main__.py` | 5 | `cli:main` 依存 |
| `tests/test_cli.py` | 3,253 | CLI テスト |
| `prefab_sentinel/bridge_check.py` | 240 | CLI 専用（MCP/他から未使用） |
| `tests/test_bridge_check.py` | ~270 | bridge_check テスト |

### 他テストファイルの CLI 依存除去

以下のテストファイルは `cli.py` を import しており、CLI 関連テストクラス/メソッドを削除する必要がある:

| ファイル | 影響箇所 | 対応 |
|---------|---------|------|
| `tests/test_editor_bridge.py` | `TestCliEditorSubcommands` クラス（23箇所で `build_parser` を import） | クラスごと削除 |
| `tests/test_mcp_server.py` | `TestCLIServeCommand` クラス（2箇所で `build_parser` を import） | クラスごと削除 |
| `tests/test_patch_revert.py` | `PatchRevertCliTests` クラス（`cli.main(argv)` 経由のE2Eテスト） | クラスごと削除 |
| `tests/test_integration_tests.py` | `CliIntegrationTestsTests` クラス（単一メソッドのみ） | クラスごと削除 |

### orchestrator 変更

- `suggest_ignore_guids()` メソッド削除（CLI 専用、MCP 未移植）
- `tests/test_orchestrator.py` と `tests/test_services.py` から `suggest_ignore_guids` テスト削除

### pyproject.toml 変更

- `prefab-sentinel` エントリポイント削除（line 20）
- `description` を MCP 中心に変更（line 8: "CLI for safe Unity..." → "Unity Prefab/Scene inspection and editing toolkit"）
- `[[tool.mypy.overrides]]` から `prefab_sentinel.cli` を削除（line 47）

### reporting.py の扱い

`reporting.py` は CLI 削除後に本番コードからの呼び出し元がなくなる（`cli.py` からの lazy import のみ）。ただし `test_reporting.py` が独立してカバーしており、将来 MCP ツールで利用する可能性があるため**残す**。デッドコード注記は不要（ライブラリモジュールとして維持）。

### 残すもの

| ファイル | 理由 |
|---------|------|
| `patch_revert.py` | MCP `revert_overrides` ツールが使用中 |
| `reporting.py` | `test_reporting.py` がカバー、ライブラリとして維持 |
| `smoke_batch.py` | `scripts/bridge_smoke_samples.py`・テスト・CI が使用。自前 `main()` あり |
| `smoke_history.py` | `scripts/smoke_summary_to_csv.py`・テスト・CI が使用。自前 `main()` あり |
| `bridge_smoke.py` | `smoke_batch`・`scripts/unity_bridge_smoke.py` が使用 |

## CI ワークフロー書き換え

CLI コマンドを Python モジュール直接呼び出しに置換する。

### `.github/workflows/ci.yml`

`ci.yml` は `uv` 環境で動作する。

```yaml
# Before:
uv run prefab-sentinel validate smoke-batch --targets all ...
uv run prefab-sentinel report smoke-history --inputs ...

# After:
uv run python -m prefab_sentinel.smoke_batch --targets all ...
uv run python -m prefab_sentinel.smoke_history --inputs ...
```

### `.github/workflows/ci.yml` (integration-test-contract)

```yaml
# Before:
uv run prefab-sentinel validate integration-tests --unity-command ...

# After:
uv run python scripts/unity_integration_tests.py --unity-command ...
```

`scripts/unity_integration_tests.py` は `--unity-command`, `--unity-project-path`, `--out-dir`, `--skip-deploy` を受け付ける（既存 argparse 定義で確認済み）。

### `.github/workflows/unity-integration.yml`

self-hosted ランナーで `pip install -e .` を使用（`uv` 未インストール）。

```yaml
# Before:
prefab-sentinel validate integration-tests --unity-command ...

# After:
python scripts/unity_integration_tests.py --unity-command ...
```

### `.github/workflows/unity-smoke.yml`

self-hosted ランナーで `pip install -e .` を使用（`uv` 未インストール）。

**重要:** 現在の `$args` 配列は CLI サブコマンドプレフィックス（`"validate", "smoke-batch"` / `"report", "smoke-history"`）を含む。`smoke_batch.main()` / `smoke_history.main()` はこれらを受け取らないため、`$args` からプレフィックスを除去する必要がある。

```powershell
# Before (smoke-batch):
$args = @(
    "validate",
    "smoke-batch",
    "--targets", "${{ inputs.targets }}",
    ...
)
prefab-sentinel @args

# After:
$args = @(
    "--targets", "${{ inputs.targets }}",
    ...
)
python -m prefab_sentinel.smoke_batch @args
```

```powershell
# Before (smoke-history):
$args = @(
    "report",
    "smoke-history",
    "--inputs", "reports/bridge_smoke/summary.json",
    ...
)
prefab-sentinel @args

# After:
$args = @(
    "--inputs", "reports/bridge_smoke/summary.json",
    ...
)
python -m prefab_sentinel.smoke_history @args
```

## README 変更

- CLI 使用例セクション（`prefab-sentinel` コマンド例）を削除
- MCP ツール一覧を主要インターフェースとして前面に配置
- CI の呼び出し方法を更新

## 破壊的変更

- `prefab-sentinel` CLI コマンドが消える
- `python -m prefab_sentinel` が動かなくなる
- CI ワークフローの書き換えが必要（同一コミット内で対応）

## Test Plan

- 削除後の残存テスト全パス確認
- 各修正テストファイルが個別にパスすることを確認:
  - `tests/test_editor_bridge.py`（CLI parser テスト削除後）
  - `tests/test_mcp_server.py`（serve parser テスト削除後）
  - `tests/test_patch_revert.py`（CLI E2E テスト削除後）
  - `tests/test_integration_tests.py`（CLI テスト削除後）
  - `tests/test_orchestrator.py`（suggest_ignore_guids テスト削除後）
  - `tests/test_services.py`（suggest_ignore_guids テスト削除後）
- `python -c "import prefab_sentinel"` で import エラーなし確認
- MCP 36 ツール登録テストがパス
- `python -m prefab_sentinel.smoke_batch --help` で動作確認
- `python -m prefab_sentinel.smoke_history --help` で動作確認
- `ruff check` がパス

## Version

機能削除（破壊的変更）→ minor バンプ: `uv run bump-my-version bump minor` → v0.4.0。

## File Change Summary

| ファイル | 変更内容 |
|---------|---------|
| `prefab_sentinel/cli.py` | 削除 |
| `prefab_sentinel/__main__.py` | 削除 |
| `prefab_sentinel/bridge_check.py` | 削除 |
| `tests/test_cli.py` | 削除 |
| `tests/test_bridge_check.py` | 削除 |
| `tests/test_editor_bridge.py` | `CliEditorParserTests` クラス削除 |
| `tests/test_mcp_server.py` | `TestServeCliParser` クラス削除 |
| `tests/test_patch_revert.py` | `PatchRevertCliTests` クラス削除 |
| `tests/test_integration_tests.py` | `CliIntegrationTestsTests` クラス削除 |
| `prefab_sentinel/orchestrator.py` | `suggest_ignore_guids()` 削除 |
| `tests/test_orchestrator.py` | suggest_ignore_guids テスト削除 |
| `tests/test_services.py` | suggest_ignore_guids テスト削除 |
| `pyproject.toml` | エントリポイント削除 + description 変更 + mypy override 削除 |
| `.github/workflows/ci.yml` | CLI → Python モジュール直接呼び出し |
| `.github/workflows/unity-integration.yml` | CLI → scripts 直接呼び出し（`python` not `uv`） |
| `.github/workflows/unity-smoke.yml` | CLI → smoke_batch/smoke_history モジュール呼び出し + `$args` プレフィックス除去 |
| `README.md` | CLI セクション削除、MCP 中心に書き換え |
