# deploy_bridge プラグインモード対応 + BridgeVersion 自動同期 設計

**日付:** 2026-03-28
**由来:** `report_20260328_deploy_bridge_plugin_mode.md`

---

## 概要

2 つの改善:

1. **Bridge ファイルを wheel にバンドル** — Plugin モード (`/plugin` 経由) で `deploy_bridge` が動作するようにする
2. **BridgeVersion 自動同期** — `bump-my-version` で Python バージョンと C# の `BridgeVersion` 定数を同期する

---

## 1: Bridge ファイルを wheel にバンドル

### 問題

`deploy_bridge` は `Path(__file__).parent.parent / "tools" / "unity"` でソースファイルを探す。Plugin インストール (`uvx --from ${CLAUDE_PLUGIN_ROOT}`) では wheel パッケージ経由のため `tools/unity/` が含まれず `DEPLOY_SOURCE_NOT_FOUND` になる。

### 解決

hatch の `force-include` で `tools/unity/` を wheel 内の `prefab_sentinel/_bridge_files/` にマッピングする。

```toml
[tool.hatch.build.targets.wheel.force-include]
"tools/unity" = "prefab_sentinel/_bridge_files"
```

### パス解決

`deploy_bridge` は 2 つのパスを環境に応じて選択する:

```python
# wheel install (plugin mode): force-include でバンドル済み
bridge_dir = _Path(__file__).parent / "_bridge_files"
if not bridge_dir.is_dir():
    # editable install (dev): ソースツリーの tools/unity/
    bridge_dir = _Path(__file__).parent.parent / "tools" / "unity"
if not bridge_dir.is_dir():
    return {"success": False, "code": "DEPLOY_SOURCE_NOT_FOUND", ...}
```

これはフォールバックではなく、2 つの正当なインストールレイアウトに対応する環境検出である:
- **wheel install**: `force-include` により `_bridge_files/` が存在する
- **editable install**: hatch の editable mode は `.pth` ファイルでソースツリーを直接参照するため、`force-include` は反映されない。`tools/unity/` から読む必要がある

### テストへの影響

テストは editable install で実行される。現在のテスト (`TestDeployBridgeCleanup`) は実際の `tools/unity/` から `.cs` ファイルをコピーしているため、パス解決が `tools/unity/` にフォールバックすれば動作に変更なし。

---

## 2: BridgeVersion 自動同期

### 問題

C# 側の `BridgeVersion` 定数が手動管理で、Python バージョンと乖離する。レポート時点で Python `0.5.131` / Bridge `0.5.82` だった。

### 解決

`bump-my-version` の設定に C# ファイルを追加:

```toml
[[tool.bumpversion.files]]
filename = "tools/unity/PrefabSentinel.UnityEditorControlBridge.cs"
search = 'BridgeVersion = "{current_version}"'
replace = 'BridgeVersion = "{new_version}"'
```

### 初期同期手順

C# の `BridgeVersion` と Python の `current_version` を一致させてから bumpversion エントリを追加する。不一致のまま追加すると `search` がマッチせずバンプが失敗する。

手順 (同一コミット内で実行):
1. C# ファイルの `BridgeVersion` を `pyproject.toml` の `current_version` に書き換え
2. `pyproject.toml` に bumpversion エントリを追加
3. 以降は pre-commit hook のパッチバンプで自動同期

### pre-commit hook の更新

現在の `.git/hooks/pre-commit` は `pyproject.toml`, `.claude-plugin/plugin.json`, `uv.lock` のみをステージングする。C# ファイルも `git add` 対象に追加する:

```bash
git add pyproject.toml .claude-plugin/plugin.json uv.lock tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
```

### CLAUDE.md の更新

バージョン記述箇所が 2 → 3 箇所になるため、CLAUDE.md のバージョン管理セクションを更新する:

> バージョン記述箇所は `pyproject.toml`、`.claude-plugin/plugin.json`、`tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` の 3 箇所（`[tool.bumpversion]` で一括管理）。

---

## 変更ファイル

| ファイル | 変更 |
|---------|------|
| `pyproject.toml` | `force-include` 追加 + bumpversion エントリ追加 |
| `prefab_sentinel/mcp_server.py` | `deploy_bridge` のパス解決を環境検出方式に変更 + docstring 更新 |
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | `BridgeVersion` を `current_version` に更新 |
| `.git/hooks/pre-commit` | C# ファイルのステージング追加 |
| `CLAUDE.md` | バージョン記述箇所を 3 箇所に更新 |
| `tests/test_mcp_server.py` | パス解決テストの更新（必要に応じて） |

## スコープ外

- `tools/unity/` のディレクトリ構造変更
- ツール数の変更
