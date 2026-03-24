# Large Scope Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `validate_refs` / `where_used` のファイル I/O を `ThreadPoolExecutor` で並列化し、大規模 scope での実行時間を 3-5x 改善する。

**Architecture:** `_read_text()` からキャッシュ操作を除いた `_read_text_uncached()` を追加し、`_preload_texts()` で並列読み込み → キャッシュ格納。`scan_broken_references()` と `where_used()` のファイル一覧収集後に `_preload_texts()` を 1 行挿入するだけで、検証ロジックは変更なし。

**Tech Stack:** Python (concurrent.futures.ThreadPoolExecutor), unittest

**Spec:** `docs/superpowers/specs/2026-03-25-large-scope-perf-design.md`

**重要な既存コードメモ:**
- `_read_text()`: `reference_resolver.py:71-82`。`decode_text_file(path)` を呼び、`UnicodeDecodeError` で `_text_cache[path] = None` + `_unreadable_paths.add(path)`。
- `_collect_scope_files()`: `reference_resolver.py:123`。引数は `(scope_path, exclude_patterns)`。
- `scan_broken_references()`: `reference_resolver.py:318` で `files = self._collect_scope_files(...)` → ループ。
- `where_used()`: `reference_resolver.py:625` で `files = self._collect_scope_files(...)` → ループ。

---

### Task 1: `_read_text_uncached` + `_preload_texts` + テスト

**Files:**
- Modify: `prefab_sentinel/services/reference_resolver.py:71-82` (付近に追加)
- Modify: `tests/test_services.py` (ReferenceResolverServiceTests に追加)

- [ ] **Step 1: テスト追加 — `_preload_texts` が `_text_cache` を埋めること**

`tests/test_services.py` の `ReferenceResolverServiceTests` クラスに追加:

```python
def test_preload_texts_populates_cache(self) -> None:
    """_preload_texts should populate _text_cache for multiple files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _create_sample_project(root)
        svc = ReferenceResolverService(project_root=root)

        files = [
            root / "Assets" / "Base.prefab",
            root / "Assets" / "Variant.prefab",
        ]
        svc._preload_texts(files)

        for f in files:
            self.assertIn(f, svc._text_cache)
            self.assertIsNotNone(svc._text_cache[f])

def test_preload_texts_handles_unreadable(self) -> None:
    """_preload_texts should mark unreadable files in _unreadable_paths."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _create_sample_project(root)
        svc = ReferenceResolverService(project_root=root)

        # Create a binary file that will fail decode
        bad = root / "Assets" / "bad.prefab"
        bad.write_bytes(b"\x80\x81\x82\x83" * 100)

        svc._preload_texts([bad])

        self.assertIn(bad, svc._unreadable_paths)
        self.assertIsNone(svc._text_cache[bad])

def test_preload_texts_idempotent(self) -> None:
    """Calling _preload_texts twice should not re-read cached files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _create_sample_project(root)
        svc = ReferenceResolverService(project_root=root)

        files = [root / "Assets" / "Base.prefab"]
        svc._preload_texts(files)
        original_text = svc._text_cache[files[0]]

        # Modify file on disk — preload should NOT re-read
        files[0].write_text("modified", encoding="utf-8")
        svc._preload_texts(files)

        self.assertEqual(svc._text_cache[files[0]], original_text)

def test_preload_texts_empty_list(self) -> None:
    """_preload_texts with empty list should not raise."""
    svc = ReferenceResolverService(project_root=Path("/fake"))
    svc._preload_texts([])  # Should not raise
```

- [ ] **Step 2: テスト実行 — FAIL 確認**

Run: `uv run --extra test python -m unittest tests.test_services.ReferenceResolverServiceTests.test_preload_texts_populates_cache -v`
Expected: FAIL (`AttributeError: 'ReferenceResolverService' object has no attribute '_preload_texts'`)

- [ ] **Step 3: `_read_text_uncached` + `_preload_texts` 実装**

`prefab_sentinel/services/reference_resolver.py` の `_read_text()` メソッド（L71-82）の直後に追加:

```python
    def _read_text_uncached(self, path: Path) -> str | None:
        """Read file text without touching caches (for parallel preload)."""
        try:
            return decode_text_file(path)
        except UnicodeDecodeError:
            return None

    def _preload_texts(
        self, paths: list[Path], max_workers: int = 10,
    ) -> None:
        """Pre-populate ``_text_cache`` by reading files in parallel."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        uncached = [
            p for p in paths
            if p not in self._text_cache and p not in self._unreadable_paths
        ]
        if not uncached:
            return
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(uncached)),
        ) as pool:
            futures = {
                pool.submit(self._read_text_uncached, p): p for p in uncached
            }
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

`import` は `_preload_texts` 内でローカル実行する（`concurrent.futures` はこのメソッドでしか使わない）。

- [ ] **Step 4: テスト実行 — 4 件全 PASS**

Run: `uv run --extra test python -m unittest tests.test_services.ReferenceResolverServiceTests.test_preload_texts_populates_cache tests.test_services.ReferenceResolverServiceTests.test_preload_texts_handles_unreadable tests.test_services.ReferenceResolverServiceTests.test_preload_texts_idempotent tests.test_services.ReferenceResolverServiceTests.test_preload_texts_empty_list -v`
Expected: 4 tests PASS

- [ ] **Step 5: コミット**

```
feat(perf): add _preload_texts for parallel file I/O
```

---

### Task 2: `scan_broken_references` / `where_used` に統合 + 検証

**Files:**
- Modify: `prefab_sentinel/services/reference_resolver.py:318` (scan_broken_references)
- Modify: `prefab_sentinel/services/reference_resolver.py:625` (where_used)
- Modify: `tasks/todo.md`

- [ ] **Step 1: `scan_broken_references()` にプリロード挿入**

`prefab_sentinel/services/reference_resolver.py` L318 の `files = self._collect_scope_files(...)` の直後に 1 行追加:

```python
        files = self._collect_scope_files(scope_path, exclude_patterns)
        self._preload_texts(files)  # ← 追加
        scan_project_root = self._resolve_scan_project_root(scope_path)
```

- [ ] **Step 2: `where_used()` にプリロード挿入**

`prefab_sentinel/services/reference_resolver.py` L625 の `files = self._collect_scope_files(...)` の直後に 1 行追加:

```python
        files = self._collect_scope_files(scan_scope_path, exclude_patterns)
        self._preload_texts(files)  # ← 追加
        scanned_files = 0
```

- [ ] **Step 3: 全テスト実行 — 回帰なし確認**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: 全テスト PASS（既存 1136 件 + 新規 4 件）

振る舞い変更はないので、既存の `scan_broken_references` / `where_used` テストはそのまま pass する。

- [ ] **Step 4: tasks/todo.md に完了記録**

- [ ] **Step 5: コミット**

```
feat(perf): integrate parallel preload into validate_refs and where_used
```

---
