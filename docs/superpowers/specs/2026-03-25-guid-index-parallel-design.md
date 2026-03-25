# GUID Index Parallel Build — 設計仕様

**日付**: 2026-03-25
**スコープ**: `collect_project_guid_index()` の `.meta` ファイル I/O 並列化

---

## 背景

PF-TEST プロジェクト（8013 GUID）で `_guid_map()` が 47.24 秒を要した。プロファイリングの結果、`_scan_meta_files()` 内の逐次 `extract_meta_guid()` 呼び出し（= `decode_text_file()` per `.meta`）が律速。

先行して実装した `_preload_texts()` の並列ベンチマークで WSL2 /mnt/d 上でも 5.6x の効果を確認済み（200 ファイル: 0.79s → 0.14s）。同じパターンを `.meta` 読み込みに適用する。

## 設計方針

- **I/O のみ並列化**: `.meta` ファイルの読み込み + GUID 抽出を `ThreadPoolExecutor` で並列実行。
- **Phase 分離**: パス収集（`os.walk`、逐次）→ GUID 抽出（並列）→ index 構築（逐次）。メインスレッドで dict 更新するためロック不要。
- **API 変更なし**: `collect_project_guid_index()` の公開シグネチャは変更しない。
- **新規依存なし**: 標準ライブラリ `concurrent.futures` のみ。

## 変更内容

### `unity_assets.py` — `_scan_meta_files()` の並列化

現在の実装:

```python
def _scan_meta_files(scan_root, excluded, index):
    for root, dirnames, filenames in os.walk(scan_root):
        dirnames[:] = [d for d in dirnames if d.lower() not in excluded]
        for filename in filenames:
            if not filename.lower().endswith(".meta"):
                continue
            meta = Path(root) / filename
            try:
                guid = extract_meta_guid(meta)
            except UnicodeDecodeError:
                continue
            if not guid:
                continue
            index[guid] = meta.with_suffix("")
```

変更後:

```python
def _scan_meta_files(scan_root, excluded, index):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Phase 1: collect .meta paths (sequential, fast)
    meta_paths: list[Path] = []
    for root, dirnames, filenames in os.walk(scan_root):
        dirnames[:] = [d for d in dirnames if d.lower() not in excluded]
        for filename in filenames:
            if filename.lower().endswith(".meta"):
                meta_paths.append(Path(root) / filename)

    if not meta_paths:
        return

    # Phase 2: parallel GUID extraction
    with ThreadPoolExecutor(
        max_workers=min(10, len(meta_paths)),
    ) as pool:
        futures = {
            pool.submit(_extract_guid_safe, p): p for p in meta_paths
        }
        for future in as_completed(futures):
            path = futures[future]
            guid = future.result()
            if guid:
                index[guid] = path.with_suffix("")
```

### `unity_assets.py` — `_extract_guid_safe()` 追加

`extract_meta_guid()` のスレッドセーフラッパー。例外を握りつぶして `None` を返す。

```python
def _extract_guid_safe(meta_path: Path) -> str | None:
    """Extract GUID from .meta file, returning None on any read failure."""
    try:
        return extract_meta_guid(meta_path)
    except (UnicodeDecodeError, OSError):
        return None
```

`extract_meta_guid()` 自体は `decode_text_file(path)` + 正規表現マッチのみで、インスタンス変数にアクセスしない純粋関数。スレッドセーフ。

### スレッドセーフティ

- `_extract_guid_safe()` は引数のファイルを読むだけ。共有状態へのアクセスなし。
- `index` dict への書き込みは `as_completed()` ループ内でメインスレッドが逐次実行。競合なし。
- `_preload_texts()` と同一のパターン。

## エッジケース

- **`.meta` ファイル 0 件**: `meta_paths` が空なので早期 return。`ThreadPoolExecutor` は生成されない。
- **デコードエラー**: `_extract_guid_safe()` が `None` を返し、`index` に追加されない。既存動作と同一。
- **ファイル消失 / パーミッションエラー**: `OSError` を捕捉して `None` を返す。
- **`Library/PackageCache`**: `collect_project_guid_index()` が `_scan_meta_files` を 2 回呼ぶ（Assets + PackageCache）。両方とも並列化される。

## 期待効果

- **8013 GUID プロジェクト**: 47s → ~9s（5x 改善）
- **小規模プロジェクト**: オーバーヘッド無視できる

## 影響範囲

| ファイル | 変更内容 |
|---------|---------|
| `prefab_sentinel/unity_assets.py` | `_extract_guid_safe()` 追加、`_scan_meta_files()` を phase-separated parallel に変更 |
| `tests/test_services.py` | GUID index 並列構築のテスト追加 |

## テスト計画

- `test_scan_meta_files_parallel_populates_index`: 複数 `.meta` ファイルから GUID が正しく抽出されること
- `test_scan_meta_files_handles_unreadable_meta`: デコード不可 `.meta` がスキップされること
- `test_scan_meta_files_empty_dir`: 空ディレクトリで例外が発生しないこと

既存の `collect_project_guid_index` を使うテストは振る舞い変更なしで全 pass する。

## やらないこと

- GUID index のディスクキャッシュ（将来拡張）
- `os.walk` 自体の並列化（ディレクトリ列挙は高速で律速ではない）
- `max_workers` のユーザー設定化（固定値 10 で十分）
