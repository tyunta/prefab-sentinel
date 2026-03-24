# Completion Matrix Optimization Design

到達度マトリクスの残ギャップ（80〜95%）を 100% に詰める実装最適化。

## Scope

3 件の変更を **ボトムアップ順** で実施する:

1. Orchestrator invalidate API（ステートフル 80% → 100%）
2. Session scope フォールバック（プロジェクトスコープ 85% → 100%）
3. `find_referencing_assets` 直接ペイロード化（MCP サーバー 95% → 100%）

破壊的変更（Section 3）を含むため、完了時に `uv run bump-my-version bump minor` で minor バージョンバンプを行う。

## 1. Orchestrator Invalidate API

### Problem

watcher がファイル変更を検知すると、session は orchestrator を `None` にして再生成する。orchestrator 再生成は GUID インデックス再構築を伴い重い（大規模プロジェクトで 1〜3 秒）。`.prefab` 変更で GUID インデックスまで捨てるのは過剰。

### Design

各サービスに自身のキャッシュをクリアするメソッドを追加し、`Phase1Orchestrator` がそれに委譲する。orchestrator がサービスの `_` プレフィックス属性を直接操作しない。

#### サービス層メソッド

```python
# reference_resolver.py
class ReferenceResolverService:
    def invalidate_text_cache(self, path: Path | None = None) -> None:
        """テキストキャッシュをクリア。path 指定で単一ファイル、None で全件。"""
        if path is None:
            self._text_cache.clear()
            self._local_id_cache.clear()
            self._unreadable_paths.clear()
        else:
            self._text_cache.pop(path, None)
            self._local_id_cache.pop(path, None)
            self._unreadable_paths.discard(path)

    def invalidate_guid_index(self) -> None:
        """GUID インデックスをクリア（次回アクセス時に再構築）。"""
        self._guid_index_cache.clear()
```

```python
# serialized_object.py — 既存の _clear_before_cache() を公開名で追加
class SerializedObjectService:
    def invalidate_before_cache(self) -> None:
        """patch dry-run の before キャッシュをクリア。"""
        self._before_cache = None  # dict | None 型。既存 _clear_before_cache() と同じ
```

#### Orchestrator 委譲メソッド

```python
# orchestrator.py
class Phase1Orchestrator:
    def invalidate_text_cache(self, path: Path | None = None) -> None:
        self.reference_resolver.invalidate_text_cache(path)

    def invalidate_guid_index(self) -> None:
        self.reference_resolver.invalidate_guid_index()

    def invalidate_before_cache(self) -> None:
        self.serialized_object.invalidate_before_cache()
```

### Invalidation routing

| トリガー | session 動作 | orchestrator 動作 |
|---------|-------------|------------------|
| `.meta` 変更 | orchestrator 再生成（既存） | 全キャッシュリセット（再生成で暗黙クリア） |
| `.prefab`/`.unity`/`.asset`/`.mat` 変更 | `invalidate_asset_caches(path)` 呼び出し | `invalidate_text_cache(path)` + `invalidate_before_cache()` |
| `.cs` 変更 | `invalidate_script_map()`（既存）+ 全 SymbolTree クリア | —（text cache は Unity asset のみ対象、`.cs` は含まない） |

**`.cs` 変更で SymbolTree も全クリアする理由**: スクリプト名が変わると SymbolTree 内の MonoBehaviour ノードの `script_name` が stale になる。スクリプト GUID → キャッシュ済み SymbolTree の逆引きインデックスは無いため、全件クリアが安全。

**watcher の `elif` 構造について**: `.meta` と `.cs` が同一バッチで変更された場合、`elif` により `.cs` パスの SymbolTree クリアはスキップされる。これは問題ない — `.meta` トリガーで orchestrator が再生成され、script_name_map もクリアされるため。SymbolTree は asset 変更ループ（既存）で個別にクリアされる。

**text cache と `.cs` の関係**: `ReferenceResolverService._text_cache` は `_read_text()` 経由で Unity asset ファイル（`.prefab`, `.unity`, `.asset`, `.mat`, `.meta`）の内容をキャッシュする。`.cs` ファイルは参照スキャン対象外のため text cache に入らず、`.cs` 変更時の text cache invalidation は不要。

### Changes

- `reference_resolver.py`: `invalidate_text_cache()`, `invalidate_guid_index()` 追加
- `serialized_object.py`: `invalidate_before_cache()` 追加（既存 `_clear_before_cache()` の公開版）
- `orchestrator.py`: 3 委譲メソッド追加
- `session.py`: `invalidate_asset_caches(path)` 追加、`invalidate_script_map()` に SymbolTree 全クリア追加
- `watcher.py`: asset 変更時のコールバック先を `invalidate_asset_caches()` に変更

## 2. Session Scope フォールバック

### Problem

`activate_project(scope)` で設定した scope が MCP ツール呼び出しに自動適用されない。毎回 `scope` パラメータを明示する必要がある。

### Design

`ProjectSession` に scope 解決メソッドを追加:

```python
class ProjectSession:
    def resolve_scope(self, explicit_scope: str | None) -> str | None:
        """明示 scope があればそれを返す。なければ session scope をフォールバック。"""
        if explicit_scope is not None:
            return explicit_scope
        return str(self._scope) if self._scope is not None else None
```

**型変換の注意**: `self._scope` は `Path | None` 型。orchestrator のメソッドは `scope: str | None` を受け取るため、`str()` で変換する。

### Rules

- 明示 `scope` > session scope（上書き可能）
- `activate_project` 未実行 + `scope` 省略 → `None`（制限なし）
- session scope は `activate_project` の再呼び出しで更新可能

### Target tools

実際に `scope` パラメータを持つ MCP ツール（**4 件**）:

- `validate_refs` — `scope: str`（必須）
- `find_referencing_assets` — `scope: str | None`
- `validate_field_rename` — `scope: str | None`
- `check_field_coverage` — `scope: str`（必須）

`inspect_wiring` と `inspect_variant` は `scope` パラメータを持たない（`path` で単一ファイルを指定する）。変更対象外。

### Required scope のエッジケース

`validate_refs` と `check_field_coverage` は `scope` が必須（ディレクトリスキャンを行うため `None` は危険）。`resolve_scope()` の結果が `None` の場合:

- MCP ツール側で `scope` パラメータの `str` 型制約によりバリデーションエラー（MCP SDK が弾く）
- つまり `activate_project` 未実行 + `scope` 省略 → 既存と同じエラー挙動。変更なし

`find_referencing_assets` と `validate_field_rename` は `scope: str | None` なので `None` は許容（プロジェクトルート全体を検索）。

### activate_project diagnostics

成功時に scope フォールバックの説明と対象ツール一覧を diagnostics に追加:

```python
diagnostics.append({
    "message": (
        f"Scope '{scope}' will be used as default for: "
        "validate_refs, find_referencing_assets, validate_field_rename, check_field_coverage."
    ),
    "severity": "info",
})
```

### Changes

- `session.py`: `resolve_scope()` メソッド追加
- `mcp_server.py`: 対象 4 ツールで `session.resolve_scope(scope)` を適用、`activate_project` diagnostics 追加

## 3. `find_referencing_assets` 直接ペイロード化

### Problem

CLAUDE.md API 規約では参照系ツールは直接ペイロードを返すが、`find_referencing_assets` はエンベロープで返している。

### Data access path

`inspect_where_used()` の返却構造:

```python
resp.data = {
    "asset_or_guid": ...,
    "scope": ...,
    "steps": [{
        "step": "where_used",
        "result": {
            "data": {
                "usages": [...],           # ← ここに実データ
                "usage_count": N,          # len(usages) + truncated_usages
                "returned_usages": M,      # len(usages)
                "truncated_usages": K,     # 切り詰められた件数（int）
            }
        }
    }]
}
```

`resp.data.get("usages")` では取得できない。正しいパスは `resp.data["steps"][0]["result"]["data"]`。

### Design

orchestrator の `inspect_where_used()` を経由せず、`reference_resolver.where_used()` を直接呼び出してフラットなレスポンスを取得する:

```python
# mcp_server.py
orch = session.get_orchestrator()
resolved_scope = session.resolve_scope(scope)

step = orch.reference_resolver.where_used(
    asset_or_guid=asset_or_guid,
    scope=resolved_scope,
    max_usages=max_results,
)

if not step.success:
    raise ToolError(step.message)

usages = step.data.get("usages", [])
return {
    "matches": usages,
    "target": asset_or_guid,
    "metadata": {
        "total_count": step.data.get("usage_count", len(usages)),
        "truncated": step.data.get("truncated_usages", 0) > 0,
        "scope": str(resolved_scope) if resolved_scope else None,
    },
}
```

### Rules

- 該当なし → 空 `matches` 配列（エラーではない）
- 結果切り詰め → `metadata.truncated: true` + `metadata.total_count`
- インフラエラー → MCP `ToolError`
- orchestrator の `inspect_where_used()` は変更なし（CLI や他の呼び出し元が使用）

### Breaking change

レスポンス形式が変わる。既存の MCP クライアントが `result["data"]["usages"]` を参照している場合は `result["matches"]` に変更が必要。

### Changes

- `mcp_server.py`: `find_referencing_assets` ツールのレスポンス変換ロジック
- `CLAUDE.md`: API 規約セクションの orchestrator 系ツール一覧から `find_referencing_assets` を参照系に移動

## Test Plan

### Unit tests

- `test_reference_resolver.py`: `invalidate_text_cache(path)` — 単一ファイルクリア + `_unreadable_paths` からも除去されること
- `test_reference_resolver.py`: `invalidate_text_cache(None)` — 全件クリア（`_text_cache`, `_local_id_cache`, `_unreadable_paths`）
- `test_reference_resolver.py`: `invalidate_guid_index()` — `_guid_index_cache` クリア
- `test_serialized_object.py`: `invalidate_before_cache()` — `_before_cache` が `None` にリセット（`None` 状態からの呼び出しも安全）
- `test_orchestrator.py`: 3 委譲メソッドがサービスに到達すること
- `test_session.py`: `resolve_scope()` — 明示 > session > None の優先順位
- `test_session.py`: `invalidate_asset_caches()` — orchestrator 再生成されず、text cache + before cache のみクリア
- `test_session.py`: `invalidate_script_map()` 拡張 — SymbolTree 全クリア（text cache は不要）
- `test_watcher.py`: `.prefab` 変更で `invalidate_asset_caches()` が呼ばれること
- `test_mcp_server.py`: `find_referencing_assets` が直接ペイロード（`matches` 配列）を返すこと
- `test_mcp_server.py`: scope フォールバックが 4 ツールで動作すること

### Regression

- 既存テスト全パス（現在 1200 件）

## File Change Summary

| ファイル | 変更内容 |
|---------|---------|
| `services/reference_resolver.py` | `invalidate_text_cache()`, `invalidate_guid_index()` 追加 |
| `services/serialized_object.py` | `invalidate_before_cache()` 追加 |
| `orchestrator.py` | 3 委譲メソッド追加 |
| `session.py` | `resolve_scope()` 追加、`invalidate_asset_caches()` 追加、`invalidate_script_map()` 拡張 |
| `watcher.py` | asset 変更コールバック先の変更 |
| `mcp_server.py` | scope フォールバック適用（4 ツール）、`find_referencing_assets` 直接ペイロード化、`activate_project` diagnostics |
| `CLAUDE.md` | API 規約セクション: `find_referencing_assets` を orchestrator 系から参照系に移動 |
| `README.md` | MCP ツールドキュメントの `find_referencing_assets` レスポンス形式更新 |
| `docs/ROADMAP_SERENA_FOR_UNITY.md` | 到達度マトリクス更新 |
| `tests/test_reference_resolver.py` | invalidate テスト追加 |
| `tests/test_serialized_object.py` | invalidate_before_cache テスト追加 |
| `tests/test_orchestrator.py` | 委譲メソッドテスト追加 |
| `tests/test_session.py` | resolve_scope + invalidate_asset_caches + invalidate_script_map 拡張テスト追加 |
| `tests/test_watcher.py` | asset 変更時の挙動テスト追加 |
| `tests/test_mcp_server.py` | レスポンス形式 + scope フォールバックテスト追加 |
| `pyproject.toml` | minor バージョンバンプ（`uv run bump-my-version bump minor`） |
