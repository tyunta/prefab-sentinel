# Large Scope Performance — 設計仕様

**日付**: 2026-03-25
**スコープ**: `validate_refs` / `where_used` のファイル I/O 並列化

---

## 背景

MCP 統合テスト (`report_mcp_integration_test_20260324.md`) で、`validate_refs` が 89 ファイル / 2490 refs の scope に対して約 15 秒を要した。プロファイリングの結果、ボトルネックは sequential file I/O（~6 files/sec）であることが判明。

キャッシュは既に適切に実装されている（GUID index, テキスト, ローカル ID の 3 層キャッシュ）。アルゴリズムの問題ではなく、純粋な I/O 待ちが律速。

## 設計方針

- **I/O のみ並列化**: ファイル読み込みを `ThreadPoolExecutor` で並列実行し、検証ロジックは既存の逐次処理のまま維持する。
- **API 変更なし**: `scan_broken_references()` と `where_used()` の公開 API は sync のまま。呼び出し元の変更不要。
- **ロック不要**: 並列フェーズ（読み込み）と逐次フェーズ（検証）を分離するため、`_text_cache` への競合アクセスが発生しない。
- **新規依存なし**: 標準ライブラリ `concurrent.futures` のみ使用。

## 変更内容

### 1. `reference_resolver.py` — 並列プリロード

#### `_read_text_uncached(path: Path) -> str | None`

既存の `_read_text()` からキャッシュ操作を除いた純粋な I/O メソッド。`decode_text_file(path)` を呼び、テキストを返す。`decode_text_file()` は内部で UTF-8 → CP932 のフォールバックを行い、両方失敗した場合に `UnicodeDecodeError` を送出する。

`_read_text_uncached()` はこの `UnicodeDecodeError` を捕捉し `None` を返す。`FileNotFoundError` は捕捉しない（既存の `_read_text()` と同じ振る舞い — ファイル消失は呼び出し元に伝播する）。ただし並列実行中のファイル消失に備え、`_preload_texts()` 側で `OSError` を捕捉する（後述）。

既存の `_read_text()` の実際の動作:
1. `_text_cache.get(path)` をチェック — `None` 以外なら返す
2. `path in _unreadable_paths` をチェック — `True` なら `None`（`_text_cache` にも `None` が入っている）
3. `decode_text_file(path)` を呼ぶ（UTF-8 → CP932 フォールバック）
4. 成功: `_text_cache[path] = text`
5. 失敗（`UnicodeDecodeError`）: `_text_cache[path] = None` + `_unreadable_paths.add(path)`

**注意**: `_text_cache` は `dict[Path, str | None]` 型。デコード失敗時も `None` を格納する。`_read_text_uncached()` はステップ 3 のみを行う。

#### `_preload_texts(paths: list[Path], max_workers: int = 10) -> None`

未キャッシュのファイルを並列読み込みし、`_text_cache` に格納する。

```python
def _preload_texts(self, paths: list[Path], max_workers: int = 10) -> None:
    uncached = [p for p in paths if p not in self._text_cache and p not in self._unreadable_paths]
    if not uncached:
        return
    with ThreadPoolExecutor(max_workers=min(max_workers, len(uncached))) as pool:
        futures = {pool.submit(self._read_text_uncached, p): p for p in uncached}
        for future in as_completed(futures):
            path = futures[future]
            try:
                text = future.result()
            except OSError:
                text = None
            if text is not None:
                self._text_cache[path] = text
            else:
                self._text_cache[path] = None
                self._unreadable_paths.add(path)
```

**エラーハンドリング**: `_read_text_uncached()` が `UnicodeDecodeError` を捕捉して `None` を返すのに対し、`OSError`（`FileNotFoundError`, `PermissionError` 等）は並列実行中のファイルシステム変動に備えて `_preload_texts()` 側で捕捉する。失敗時は `_text_cache[path] = None` と `_unreadable_paths.add(path)` で `_read_text()` と同じ状態にする。

**スレッドセーフティ**: `_read_text_uncached()` はインスタンス変数にアクセスしない（引数のファイルを読むだけ）。`_text_cache` / `_unreadable_paths` の更新は `as_completed()` ループ内でメインスレッドが逐次実行するため、競合は発生しない。

#### `scan_broken_references()` / `where_used()` への統合

各メソッドのファイル一覧収集後、検証ループの前に 1 行追加:

```python
files = self._collect_scope_files(scope_path, exclude_patterns)
self._preload_texts(files)  # ← 追加
# 既存の検証ループ（変更なし）
for f in files:
    text = self._read_text(f)  # キャッシュヒット
    ...
```

### 2. `_read_text()` との関係

`_read_text()` 自体は変更しない。`_preload_texts()` 実行後はキャッシュヒットするので、既存の逐次ループのコードパスに影響はない。`_preload_texts()` を呼ばなくても従来通り動作する（フォールバック）。

`_preload_texts()` は失敗時に `_text_cache[path] = None` を設定するので、`_read_text()` の既存チェック（`cached = self._text_cache.get(path)` → `if cached is not None or path in self._unreadable_paths: return cached`）と完全に整合する。

## エッジケース

- **ファイル数 0**: `uncached` が空なので `return` して終了。`ThreadPoolExecutor` は生成されない。
- **ファイル数 1**: `max_workers=1` で実行。並列化のオーバーヘッドは無視できる。
- **デコードエラー**: `_read_text_uncached()` が `None` を返し、`_text_cache[path] = None` + `_unreadable_paths` に追加。既存の `_read_text()` と同一の状態になる。
- **ファイルが途中で消える / パーミッションエラー**: `_preload_texts()` が `OSError` を捕捉し、unreadable として記録する。WSL/ネットワークパスでの一時的アクセス不可にも対応。
- **外部参照先ファイル**: `scan_broken_references()` の検証ループ中に外部ファイルの `_read_text()` が呼ばれるケースがある。これらは scope 外なのでプリロード対象外だが、`_text_cache` に個別キャッシュされるので 2 回目以降はヒットする。大量の外部参照先がある場合の追加最適化は将来拡張とする。
- **`invalidate_text_cache(path)` 後の再アクセス**: invalidation は `_text_cache.pop(path, None)` + `_unreadable_paths.discard(path)` を実行するので、次の `_read_text()` で再読み込みが行われる。`_preload_texts()` の結果も正しく invalidation される。

## 期待効果

- **89 ファイル scope**: 15 秒 → 3-5 秒（3-5x 改善、I/O 並列化分）
- **3 ファイル scope**: 3 秒 → 変化なし（元々 I/O が少ない）
- **ファイル数 0-1**: オーバーヘッドなし

## 影響範囲

| ファイル | 変更内容 |
|---------|---------|
| `prefab_sentinel/services/reference_resolver.py` | `_read_text_uncached()`, `_preload_texts()` 追加。`scan_broken_references()`, `where_used()` に 1 行追加 |
| `tests/test_services.py` | `_preload_texts` のテスト追加 |

## テスト計画

`tests/test_services.py` の `ReferenceResolverServiceTests` クラスに追加:

- `test_preload_texts_populates_cache`: 複数ファイルを `_preload_texts()` 後に `_text_cache` に格納されていること
- `test_preload_texts_handles_unreadable`: 読み込み不可ファイル混在時に `_unreadable_paths` に追加されること
- `test_preload_texts_idempotent`: 2 回呼んでも再読み込みしないこと（キャッシュ済みファイルがスキップされること）
- `test_preload_texts_empty_list`: 空リストで例外が発生しないこと

既存の `scan_broken_references` / `where_used` テストは振る舞い変更なしで全 pass する。

## やらないこと

- 検証ロジックの並列化（ロック管理が必要になり複雑化する）。
- `asyncio` / `aiofiles` への移行（API 変更が広範囲、新規依存）。
- GUID index build の並列化（既にキャッシュ済みで 2 秒程度、ROI 低い）。
- GUID index のディスクキャッシュ（将来拡張）。
- `max_workers` のユーザー設定化（固定値 10 で十分）。
- 外部参照先ファイルのプリロード（scope 外ファイルの列挙が事前に不可能）。
- `_local_id_cache` のプリロード（テキストキャッシュ後の CPU バウンド処理であり、I/O 並列化の対象外）。

## 検証基準

1. 全ユニットテスト pass
2. `_preload_texts` 後に `_text_cache` にファイルが格納されていること
3. `_preload_texts` 後の `_read_text()` がキャッシュヒットすること
4. デコードエラー / ファイル消失時に `_text_cache[path] = None` + `_unreadable_paths` に追加されること
5. 既存の `scan_broken_references()` / `where_used()` の振る舞いが変わらないこと
