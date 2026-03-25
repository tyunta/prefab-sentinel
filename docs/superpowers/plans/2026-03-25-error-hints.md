# "Did you mean...?" Error Hints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fuzzy match suggestions to `SYMBOL_NOT_FOUND`, `MAT_PROP_NOT_FOUND`, and `EDITOR_CTRL_PROPERTY_NOT_FOUND` error responses so AI agents can self-correct typos.

**Architecture:** A thin `fuzzy_match.py` module wraps `difflib.get_close_matches()`. MCP server and material inspector call it when building error responses. C# side implements Levenshtein distance for shader property suggestions.

**Tech Stack:** Python `difflib` (stdlib), C# `System.Math` for Levenshtein

**Spec:** `docs/superpowers/specs/2026-03-25-error-hints-design.md`

---

### Task 1: `fuzzy_match.py` module (Python)

**Files:**
- Create: `prefab_sentinel/fuzzy_match.py`
- Create: `tests/test_fuzzy_match.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fuzzy_match.py
from __future__ import annotations

import unittest

from prefab_sentinel.fuzzy_match import suggest_similar


class TestSuggestSimilar(unittest.TestCase):
    def test_typo_returns_correct_candidate(self) -> None:
        result = suggest_similar("MeshRendrer", ["MeshRenderer", "MeshFilter", "AudioSource"])
        self.assertEqual(result[0], "MeshRenderer")

    def test_complete_mismatch_returns_empty(self) -> None:
        result = suggest_similar("ZZZZZZZZ", ["MeshRenderer", "MeshFilter", "AudioSource"])
        self.assertEqual(result, [])

    def test_empty_candidates_returns_empty(self) -> None:
        result = suggest_similar("anything", [])
        self.assertEqual(result, [])

    def test_max_three_results(self) -> None:
        candidates = [f"item_{i}" for i in range(100)]
        result = suggest_similar("item_0", candidates)
        self.assertLessEqual(len(result), 3)

    def test_case_sensitive_matching(self) -> None:
        """difflib is case-sensitive; '_color' should not match '_Color' at high cutoff."""
        result = suggest_similar("_color", ["_Color", "_MainTex"], cutoff=0.9)
        # At cutoff=0.9, case difference may drop below threshold
        # At default cutoff=0.6, it should still match
        result_default = suggest_similar("_color", ["_Color", "_MainTex"])
        self.assertIn("_Color", result_default)

    def test_single_char_typo(self) -> None:
        result = suggest_similar("_Colr", ["_Color", "_MainTex", "_BumpMap"])
        self.assertIn("_Color", result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fuzzy_match.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prefab_sentinel.fuzzy_match'`

- [ ] **Step 3: Write minimal implementation**

```python
# prefab_sentinel/fuzzy_match.py
"""Fuzzy matching utilities for error hint suggestions."""

from __future__ import annotations

from difflib import get_close_matches
from typing import Iterable


def suggest_similar(
    word: str,
    candidates: Iterable[str],
    *,
    n: int = 3,
    cutoff: float = 0.6,
) -> list[str]:
    """Return up to *n* candidates similar to *word*.

    Uses ``difflib.SequenceMatcher`` (ratio >= *cutoff*).
    Returns an empty list when no candidate exceeds the threshold.
    """
    return get_close_matches(word, list(candidates), n=n, cutoff=cutoff)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fuzzy_match.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/fuzzy_match.py tests/test_fuzzy_match.py
git commit -m "feat: add fuzzy_match module for error hint suggestions"
```

---

### Task 2: Add suggestions to `SYMBOL_NOT_FOUND` in MCP server

**Files:**
- Modify: `prefab_sentinel/mcp_server.py` (lines 82-104 helper area + lines 423-431, 543-551, 645-653 error blocks)
- Modify: `tests/test_mcp_server.py` (existing `SYMBOL_NOT_FOUND` tests)

**Context:** There are 3 `SYMBOL_NOT_FOUND` return blocks in `mcp_server.py`:
1. `set_property` (line 423-431) — "No component found"
2. `add_component` (line 543-551) — "No game object found"
3. `remove_component` (line 645-653) — "No component found"

All 3 have the same pattern: `tree` is already built when `SymbolNotFoundError` is caught.

- [ ] **Step 1: Add `_collect_symbol_paths` helper and update the first error block**

Add after `_resolve_component_name` (around line 104) in `mcp_server.py`:

```python
    def _collect_symbol_paths(tree: SymbolTree) -> list[str]:
        """Collect all symbol paths from a tree for suggestion purposes."""
        paths: list[str] = []

        def _walk(nodes: list[SymbolNode], prefix: str) -> None:
            for node in nodes:
                path = f"{prefix}/{node.name}" if prefix else node.name
                paths.append(path)
                _walk(node.children, path)

        _walk(tree.roots, "")
        return paths
```

Add import at module level (near line 26-38, alongside other `prefab_sentinel` imports):

```python
from prefab_sentinel.fuzzy_match import suggest_similar
```

Also add `SymbolTree` to the existing `symbol_tree` import block (around line 30-35):

```python
from prefab_sentinel.symbol_tree import (
    AmbiguousSymbolError,
    SymbolKind,
    SymbolNode,
    SymbolNotFoundError,
    SymbolTree,  # add this
)
```

Update the `set_property` `SymbolNotFoundError` handler (line 423-431):

```python
        except SymbolNotFoundError:
            suggestions = suggest_similar(
                symbol_path, _collect_symbol_paths(tree),
            )
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_NOT_FOUND",
                "message": f"No component found at symbol path: {symbol_path!r}",
                "data": {
                    "asset_path": asset_path,
                    "symbol_path": symbol_path,
                    "suggestions": suggestions,
                },
                "diagnostics": [],
            }
```

- [ ] **Step 2: Update the other two `SYMBOL_NOT_FOUND` blocks identically**

`add_component` (line 543-551) — same pattern, message says "No game object found":

```python
        except SymbolNotFoundError:
            suggestions = suggest_similar(
                symbol_path, _collect_symbol_paths(tree),
            )
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_NOT_FOUND",
                "message": f"No game object found at symbol path: {symbol_path!r}",
                "data": {
                    "asset_path": asset_path,
                    "symbol_path": symbol_path,
                    "suggestions": suggestions,
                },
                "diagnostics": [],
            }
```

`remove_component` (line 645-653) — message says "No component found":

```python
        except SymbolNotFoundError:
            suggestions = suggest_similar(
                symbol_path, _collect_symbol_paths(tree),
            )
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_NOT_FOUND",
                "message": f"No component found at symbol path: {symbol_path!r}",
                "data": {
                    "asset_path": asset_path,
                    "symbol_path": symbol_path,
                    "suggestions": suggestions,
                },
                "diagnostics": [],
            }
```

- [ ] **Step 3: Update existing tests to verify `suggestions` key**

In `tests/test_mcp_server.py`, find the 3 existing `SYMBOL_NOT_FOUND` assertions and add a `suggestions` check after each:

For `test_set_property_symbol_not_found` (around line 672):
```python
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])
        self.assertIsInstance(result["data"]["suggestions"], list)
```

For `test_add_component_symbol_not_found` (around line 1271):
```python
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])
        self.assertIsInstance(result["data"]["suggestions"], list)
```

For `test_remove_component_symbol_not_found` (around line 1471):
```python
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])
        self.assertIsInstance(result["data"]["suggestions"], list)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mcp_server.py -k "symbol_not_found" -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add fuzzy suggestions to SYMBOL_NOT_FOUND errors"
```

---

### Task 3: Add suggestions to `MAT_PROP_NOT_FOUND` in material inspector

**Files:**
- Modify: `prefab_sentinel/material_asset_inspector.py` (around line 567-574)
- Modify: `tests/test_material_write.py` (existing `test_property_not_found`)

- [ ] **Step 1: Update `write_material_property` error path**

In `prefab_sentinel/material_asset_inspector.py`, add import at the top:

```python
from prefab_sentinel.fuzzy_match import suggest_similar
```

Update the `MAT_PROP_NOT_FOUND` block (around line 567-574):

```python
    category, before, section_name = _find_property(text, property_name)
    if category is None:
        all_names = _list_all_property_names(text)
        suggestions = suggest_similar(property_name, all_names)
        return _error_dict(
            "MAT_PROP_NOT_FOUND",
            f"Property '{property_name}' not found in {path.name}",
            data={"available_properties": all_names, "suggestions": suggestions},
            diagnostics=[{"detail": f"Available: {', '.join(all_names)}"}],
        )
```

- [ ] **Step 2: Update the existing test**

In `tests/test_material_write.py`, update `test_property_not_found` (around line 119):

```python
    def test_property_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_NonExistent", "1", dry_run=True)
            self.assertFalse(result["success"])
            self.assertEqual("MAT_PROP_NOT_FOUND", result["code"])
            # Should list available properties
            self.assertTrue(len(result["diagnostics"]) > 0)
            # Should have suggestions field
            self.assertIn("suggestions", result["data"])
            self.assertIsInstance(result["data"]["suggestions"], list)
```

- [ ] **Step 3: Add a test for a near-miss property name**

Add a new test method in `TestWriteMaterialPropertyErrors`:

```python
    def test_property_not_found_with_suggestions(self) -> None:
        """Typo in property name returns fuzzy match suggestions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_Colr", "1", dry_run=True)
            self.assertFalse(result["success"])
            self.assertEqual("MAT_PROP_NOT_FOUND", result["code"])
            self.assertIn("_Color", result["data"]["suggestions"])
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_material_write.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/material_asset_inspector.py tests/test_material_write.py
git commit -m "feat: add fuzzy suggestions to MAT_PROP_NOT_FOUND errors"
```

---

### Task 4: Add suggestions to C# `EDITOR_CTRL_PROPERTY_NOT_FOUND`

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add `suggestions` field to `EditorControlData`**

In `EditorControlData` class (around line 187, before the closing `}`):

```csharp
            // error hint suggestions
            public string[] suggestions = Array.Empty<string>();
```

- [ ] **Step 2: Add `BuildError` overload with data parameter**

After the existing `BuildError` method (around line 1406):

```csharp
        private static EditorControlResponse BuildError(string code, string message, EditorControlData data)
        {
            return new EditorControlResponse
            {
                protocol_version = ProtocolVersion,
                success = false,
                severity = "error",
                code = code,
                message = message,
                data = data,
            };
        }
```

- [ ] **Step 3: Add Levenshtein distance and SuggestSimilar methods**

Add as static methods in the `UnityEditorControlBridge` class (after `BuildError`):

```csharp
        private static int LevenshteinDistance(string a, string b)
        {
            if (string.IsNullOrEmpty(a)) return b?.Length ?? 0;
            if (string.IsNullOrEmpty(b)) return a.Length;

            var dp = new int[a.Length + 1, b.Length + 1];
            for (int i = 0; i <= a.Length; i++) dp[i, 0] = i;
            for (int j = 0; j <= b.Length; j++) dp[0, j] = j;

            for (int i = 1; i <= a.Length; i++)
            {
                for (int j = 1; j <= b.Length; j++)
                {
                    int cost = a[i - 1] == b[j - 1] ? 0 : 1;
                    dp[i, j] = Math.Min(
                        Math.Min(dp[i - 1, j] + 1, dp[i, j - 1] + 1),
                        dp[i - 1, j - 1] + cost
                    );
                }
            }
            return dp[a.Length, b.Length];
        }

        private static string[] SuggestSimilar(string word, List<string> candidates, int maxResults = 3)
        {
            if (string.IsNullOrEmpty(word) || candidates == null || candidates.Count == 0)
                return Array.Empty<string>();

            var scored = new List<(string name, int dist)>();
            foreach (var candidate in candidates)
            {
                int dist = LevenshteinDistance(word, candidate);
                int maxLen = Math.Max(word.Length, candidate.Length);
                if (maxLen > 0 && dist <= maxLen * 0.4f)
                    scored.Add((candidate, dist));
            }
            scored.Sort((a, b) => a.dist.CompareTo(b.dist));
            var result = new string[Math.Min(maxResults, scored.Count)];
            for (int i = 0; i < result.Length; i++)
                result[i] = scored[i].name;
            return result;
        }
```

- [ ] **Step 4: Add helper to collect shader property names**

Add as a static method:

```csharp
        private static List<string> CollectShaderPropertyNames(Shader shader)
        {
            var names = new List<string>();
            int count = shader.GetPropertyCount();
            for (int i = 0; i < count; i++)
                names.Add(shader.GetPropertyName(i));
            return names;
        }
```

- [ ] **Step 5: Update the two `EDITOR_CTRL_PROPERTY_NOT_FOUND` return sites**

In `HandleGetMaterialProperty` (around line 1030):

Replace:
```csharp
            if (!string.IsNullOrEmpty(request.property_name) && properties.Count == 0)
                return BuildError("EDITOR_CTRL_PROPERTY_NOT_FOUND",
                    $"Property '{request.property_name}' not found on shader '{shader.name}'.");
```

With:
```csharp
            if (!string.IsNullOrEmpty(request.property_name) && properties.Count == 0)
                return BuildError("EDITOR_CTRL_PROPERTY_NOT_FOUND",
                    $"Property '{request.property_name}' not found on shader '{shader.name}'.",
                    new EditorControlData
                    {
                        suggestions = SuggestSimilar(request.property_name, CollectShaderPropertyNames(shader)),
                    });
```

In `HandleSetMaterialProperty` (around line 1083):

Replace:
```csharp
            if (propIdx < 0)
                return BuildError("EDITOR_CTRL_PROPERTY_NOT_FOUND",
                    $"Property '{request.property_name}' not found on shader '{shader.name}'.");
```

With:
```csharp
            if (propIdx < 0)
                return BuildError("EDITOR_CTRL_PROPERTY_NOT_FOUND",
                    $"Property '{request.property_name}' not found on shader '{shader.name}'.",
                    new EditorControlData
                    {
                        suggestions = SuggestSimilar(request.property_name, CollectShaderPropertyNames(shader)),
                    });
```

- [ ] **Step 6: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat: add Levenshtein suggestions to EDITOR_CTRL_PROPERTY_NOT_FOUND"
```

---

### Task 5: Update README and final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add error hints section to README**

In `README.md`, in section 17.8 (read-only inspection tools) or after section 17.7, add:

```markdown
### 17.8.1 エラーヒント ("Did you mean...?")

- `SYMBOL_NOT_FOUND` エラー（`set_property`, `add_component`, `remove_component`）は `data.suggestions` に類似 symbol_path のリスト（最大 3 件）を含む。
- `MAT_PROP_NOT_FOUND` エラー（`set_material_property`）は `data.suggestions` に類似プロパティ名のリスト（最大 3 件）を含む。既存の `data.available_properties`（全プロパティ名リスト）も維持される。
- `EDITOR_CTRL_PROPERTY_NOT_FOUND` エラー（`editor_get_material_property`, `editor_set_material_property`）は `data.suggestions` に類似シェーダープロパティ名のリスト（最大 3 件）を含む。
- 候補なしの場合は `suggestions` は空配列 `[]`。
- Python 側は `difflib.SequenceMatcher`、C# 側は Levenshtein 距離を使用（アルゴリズム差異あり、結果の完全一致は保証しない）。
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/test_fuzzy_match.py tests/test_material_write.py tests/test_mcp_server.py -v`
Expected: All tests PASS (pre-existing failures excluded)

- [ ] **Step 3: Verify no regressions**

Run: `uv run pytest tests/ -q --tb=no`
Expected: Failure count remains at 69 (no new failures introduced). The 69 are pre-existing.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add error hints section to README"
```
