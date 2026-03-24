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

既存の `_read_text()` からキャッシュ操作を除いた純粋な I/O メソッド。ファイルを読み込み、テキストを返す。デコードエラー時は `None` を返す。

既存の `_read_text()` は以下の処理を行う:
1. `_text_cache` をチェック（ヒットなら返す）
2. `_unreadable_paths` をチェック（既知の失敗なら `None`）
3. ファイルを読み込み（UTF-8 → CP932 フォールバック）
4. 結果を `_text_cache` に格納
5. 失敗なら `_unreadable_paths` に追加

`_read_text_uncached()` はステップ 3 のみを行い、キャッシュ操作（ステップ 1, 2, 4, 5）を省略する。

#### `_preload_texts(paths: list[Path], max_workers: int = 10) -> None`

未キャッシュのファイルを並列読み込みし、`_text_cache` に格納する。

```python
def _preload_texts(self, paths: list[Path], max_workers: int = 10) -> None:
    uncached = [p for p in paths if p not in self._text_cache and p not in self._unreadable_paths]
    if not uncached:
        return
    with ThreadPoolExecutor(max_workers=min(max_workers, len(uncached))) as pool:
        results = list(pool.map(self._read_text_uncached, uncached))
    for path, text in zip(uncached, results):
        if text is not None:
            self._text_cache[path] = text
        else:
            self._unreadable_paths.add(path)
```

**スレッドセーフティ**: `_preload_texts()` は `pool.map()` 完了後に逐次で `_text_cache` / `_unreadable_paths` を更新する。並列フェーズでは `_read_text_uncached()` が独立したファイルを読むだけでインスタンス変数にアクセスしないため、競合は発生しない。

#### `scan_broken_references()` / `where_used()` への統合

各メソッドのファイル一覧収集後、検証ループの前に 1 行追加:

```python
files = self._collect_scope_files(scope_path, extensions)
self._preload_texts(files)  # ← 追加
# 既存の検証ループ（変更なし）
for f in files:
    text = self._read_text(f)  # キャッシュヒット
    ...
```

### 2. `_read_text()` との関係

`_read_text()` 自体は変更しない。`_preload_texts()` 実行後はキャッシュヒットするので、既存の逐次ループのコードパスに影響はない。`_preload_texts()` を呼ばなくても従来通り動作する（フォールバック）。

## エッジケース

- **ファイル数 0**: `uncached` が空なので `return` して終了。`ThreadPoolExecutor` は生成されない。
- **ファイル数 1**: `max_workers=1` で実行。並列化のオーバーヘッドは無視できる。
- **デコードエラー**: `_read_text_uncached()` が `None` を返し、`_unreadable_paths` に追加される。既存の `_read_text()` と同じ振る舞い。
- **ファイルが途中で消える**: `FileNotFoundError` は `_read_text_uncached()` 内で捕捉し `None` を返す。
- **外部参照先ファイル**: `scan_broken_references()` の検証ループ中に外部ファイルの `_read_text()` が呼ばれるケースがある。これらは scope 外なのでプリロード対象外だが、`_text_cache` に個別キャッシュされるので 2 回目以降はヒットする。大量の外部参照先がある場合の追加最適化は将来拡張とする。

## 期待効果

- **89 ファイル scope**: 15 秒 → 3-5 秒（3-5x 改善、I/O 並列化分）
- **3 ファイル scope**: 3 秒 → 変化なし（元々 I/O が少ない）
- **ファイル数 0-1**: オーバーヘッドなし

## 影響範囲

| ファイル | 変更内容 |
|---------|---------|
| `prefab_sentinel/services/reference_resolver.py` | `_read_text_uncached()`, `_preload_texts()` 追加。`scan_broken_references()`, `where_used()` に 1 行追加 |
| `tests/test_reference_resolver.py` | `_preload_texts` のテスト追加 |

## やらないこと

- 検証ロジックの並列化（ロック管理が必要になり複雑化する）。
- `asyncio` / `aiofiles` への移行（API 変更が広範囲、新規依存）。
- GUID index build の並列化（既にキャッシュ済みで 2 秒程度、ROI 低い）。
- GUID index のディスクキャッシュ（将来拡張）。
- `max_workers` のユーザー設定化（固定値 10 で十分）。
- 外部参照先ファイルのプリロード（scope 外ファイルの列挙が事前に不可能）。

## 検証基準

1. 全ユニットテスト pass
2. `_preload_texts` 後に `_text_cache` にファイルが格納されていること
3. `_preload_texts` 後の `_read_text()` がキャッシュヒットすること
4. デコードエラー時に `_unreadable_paths` に追加されること
5. 既存の `scan_broken_references()` / `where_used()` の振る舞いが変わらないこと
