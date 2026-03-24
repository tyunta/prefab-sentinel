# Completion Matrix Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining 80-95% gaps in the Serena for Unity roadmap completion matrix to 100%.

**Architecture:** Bottom-up: service-level invalidation → session scope fallback → MCP response format. Each layer builds on the previous.

**Tech Stack:** Python 3.12, unittest, unittest.mock, mcp SDK

**Spec:** `docs/superpowers/specs/2026-03-24-completion-matrix-optimization-design.md`

**Test runner:** `uv run --extra test python scripts/run_unit_tests.py`

---

### Task 1: ReferenceResolverService invalidation methods

**Files:**
- Modify: `prefab_sentinel/services/reference_resolver.py:33-43`
- Test: `tests/test_services.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_services.py` — find the `ReferenceResolverServiceTests` class and add:

```python
def test_invalidate_text_cache_single_file(self) -> None:
    svc = ReferenceResolverService(project_root=Path("/fake/project"))
    path = Path("/fake/Assets/Test.prefab")
    svc._text_cache[path] = "content"
    svc._local_id_cache[path] = {"123"}
    svc._unreadable_paths.add(path)
    other = Path("/fake/Assets/Other.prefab")
    svc._text_cache[other] = "other"

    svc.invalidate_text_cache(path)

    self.assertNotIn(path, svc._text_cache)
    self.assertNotIn(path, svc._local_id_cache)
    self.assertNotIn(path, svc._unreadable_paths)
    self.assertIn(other, svc._text_cache)

def test_invalidate_text_cache_all(self) -> None:
    svc = ReferenceResolverService(project_root=Path("/fake/project"))
    path = Path("/fake/Assets/Test.prefab")
    svc._text_cache[path] = "content"
    svc._local_id_cache[path] = {"123"}
    svc._unreadable_paths.add(path)

    svc.invalidate_text_cache(None)

    self.assertEqual(len(svc._text_cache), 0)
    self.assertEqual(len(svc._local_id_cache), 0)
    self.assertEqual(len(svc._unreadable_paths), 0)

def test_invalidate_guid_index(self) -> None:
    svc = ReferenceResolverService(project_root=Path("/fake/project"))
    svc._guid_index_cache[Path("/fake")] = {"guid1": Path("/fake/a.prefab")}

    svc.invalidate_guid_index()

    self.assertEqual(len(svc._guid_index_cache), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test python -m unittest tests.test_services.ReferenceResolverServiceTests.test_invalidate_text_cache_single_file tests.test_services.ReferenceResolverServiceTests.test_invalidate_text_cache_all tests.test_services.ReferenceResolverServiceTests.test_invalidate_guid_index -v`
Expected: FAIL — `AttributeError: 'ReferenceResolverService' object has no attribute 'invalidate_text_cache'`

- [ ] **Step 3: Implement invalidation methods**

Add after `__init__` in `prefab_sentinel/services/reference_resolver.py` (after line 43):

```python
def invalidate_text_cache(self, path: Path | None = None) -> None:
    """Clear text/localID caches. *path*=None clears all."""
    if path is None:
        self._text_cache.clear()
        self._local_id_cache.clear()
        self._unreadable_paths.clear()
    else:
        self._text_cache.pop(path, None)
        self._local_id_cache.pop(path, None)
        self._unreadable_paths.discard(path)

def invalidate_guid_index(self) -> None:
    """Clear the GUID index cache."""
    self._guid_index_cache.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_services.ReferenceResolverServiceTests.test_invalidate_text_cache_single_file tests.test_services.ReferenceResolverServiceTests.test_invalidate_text_cache_all tests.test_services.ReferenceResolverServiceTests.test_invalidate_guid_index -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/services/reference_resolver.py tests/test_services.py
git commit -m "feat(cache): add invalidation methods to ReferenceResolverService"
```

---

### Task 2: SerializedObjectService invalidation method

**Files:**
- Modify: `prefab_sentinel/services/serialized_object.py:2696-2698`
- Test: `tests/test_services.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_services.py` — find `SerializedObjectServiceTests` and add:

```python
def test_invalidate_before_cache_from_populated(self) -> None:
    svc = SerializedObjectService(
        project_root=Path("/fake/project"), prefab_variant=MagicMock(),
    )
    svc._before_cache = {"comp:field": "value"}

    svc.invalidate_before_cache()

    self.assertIsNone(svc._before_cache)

def test_invalidate_before_cache_from_none(self) -> None:
    svc = SerializedObjectService(
        project_root=Path("/fake/project"), prefab_variant=MagicMock(),
    )
    self.assertIsNone(svc._before_cache)

    svc.invalidate_before_cache()  # should not raise

    self.assertIsNone(svc._before_cache)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test python -m unittest tests.test_services.SerializedObjectServiceTests.test_invalidate_before_cache_from_populated tests.test_services.SerializedObjectServiceTests.test_invalidate_before_cache_from_none -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement invalidation method**

Add after `_clear_before_cache` in `prefab_sentinel/services/serialized_object.py` (after line 2698):

```python
def invalidate_before_cache(self) -> None:
    """Public cache invalidation — resets before-value cache."""
    self._before_cache = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_services.SerializedObjectServiceTests.test_invalidate_before_cache_from_populated tests.test_services.SerializedObjectServiceTests.test_invalidate_before_cache_from_none -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/services/serialized_object.py tests/test_services.py
git commit -m "feat(cache): add invalidate_before_cache to SerializedObjectService"
```

---

### Task 3: Orchestrator delegation methods

**Files:**
- Modify: `prefab_sentinel/orchestrator.py:92` (after `default()`)
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests**

Add a new test class in `tests/test_orchestrator.py`:

```python
class TestInvalidationDelegation(unittest.TestCase):
    """Orchestrator invalidation delegates to services."""

    def _make_orchestrator(self) -> Phase1Orchestrator:
        return Phase1Orchestrator(
            reference_resolver=MagicMock(),
            prefab_variant=MagicMock(),
            runtime_validation=MagicMock(),
            serialized_object=MagicMock(),
        )

    def test_invalidate_text_cache_delegates(self) -> None:
        orch = self._make_orchestrator()
        path = Path("/test.prefab")
        orch.invalidate_text_cache(path)
        orch.reference_resolver.invalidate_text_cache.assert_called_once_with(path)

    def test_invalidate_text_cache_none_delegates(self) -> None:
        orch = self._make_orchestrator()
        orch.invalidate_text_cache(None)
        orch.reference_resolver.invalidate_text_cache.assert_called_once_with(None)

    def test_invalidate_guid_index_delegates(self) -> None:
        orch = self._make_orchestrator()
        orch.invalidate_guid_index()
        orch.reference_resolver.invalidate_guid_index.assert_called_once()

    def test_invalidate_before_cache_delegates(self) -> None:
        orch = self._make_orchestrator()
        orch.invalidate_before_cache()
        orch.serialized_object.invalidate_before_cache.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test python -m unittest tests.test_orchestrator.TestInvalidationDelegation -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement delegation methods**

Add to `Phase1Orchestrator` in `prefab_sentinel/orchestrator.py` (after `default()` classmethod, around line 92):

```python
# ------------------------------------------------------------------
# Cache invalidation (delegated to services)
# ------------------------------------------------------------------

def invalidate_text_cache(self, path: Path | None = None) -> None:
    """Delegate text cache invalidation to reference resolver."""
    self.reference_resolver.invalidate_text_cache(path)

def invalidate_guid_index(self) -> None:
    """Delegate GUID index invalidation to reference resolver."""
    self.reference_resolver.invalidate_guid_index()

def invalidate_before_cache(self) -> None:
    """Delegate before-cache invalidation to serialized object service."""
    self.serialized_object.invalidate_before_cache()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_orchestrator.TestInvalidationDelegation -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(cache): add invalidation delegation to Phase1Orchestrator"
```

---

### Task 4: Session invalidate_asset_caches + invalidate_script_map expansion

**Files:**
- Modify: `prefab_sentinel/session.py:191-216`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_session.py` in `TestInvalidationCascades`:

```python
def test_invalidate_asset_caches_clears_text_and_before(self) -> None:
    session = ProjectSession()
    mock_orch = MagicMock()
    session._orchestrator = mock_orch
    path = Path("/project/Assets/Test.prefab")

    session.invalidate_asset_caches(path)

    mock_orch.invalidate_text_cache.assert_called_once_with(path)
    mock_orch.invalidate_before_cache.assert_called_once()
    # orchestrator NOT re-created
    self.assertIs(session._orchestrator, mock_orch)

def test_invalidate_asset_caches_noop_without_orchestrator(self) -> None:
    session = ProjectSession()
    self.assertIsNone(session._orchestrator)
    # Should not raise
    session.invalidate_asset_caches(Path("/fake.prefab"))

def test_invalidate_script_map_clears_symbol_cache(self) -> None:
    with _tmp_prefab() as path:
        session = ProjectSession()
        session.get_symbol_tree(path, _simple_prefab_text())
        self.assertEqual(len(session._symbol_cache), 1)

        session.invalidate_script_map()

        self.assertIsNone(session._script_name_map)
        self.assertEqual(len(session._symbol_cache), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test python -m unittest tests.test_session.TestInvalidationCascades.test_invalidate_asset_caches_clears_text_and_before tests.test_session.TestInvalidationCascades.test_invalidate_asset_caches_noop_without_orchestrator tests.test_session.TestInvalidationCascades.test_invalidate_script_map_clears_symbol_cache -v`
Expected: FAIL — `AttributeError: 'ProjectSession' object has no attribute 'invalidate_asset_caches'` and assertion failure for symbol cache

- [ ] **Step 3: Implement session changes**

In `prefab_sentinel/session.py`, add `invalidate_asset_caches` and expand `invalidate_script_map`:

```python
def invalidate_script_map(self) -> None:
    """Clear only the script name map (trigger: .cs change).

    Also clears all SymbolTree entries because MonoBehaviour nodes
    reference script names from the map.
    """
    self._script_name_map = None
    self._symbol_cache.clear()
    logger.debug("Invalidated script name map + all SymbolTree entries")

def invalidate_asset_caches(self, path: Path) -> None:
    """Clear service-level caches for a single asset (trigger: asset file change).

    Unlike invalidate_guid_index, this does NOT re-create the orchestrator.
    """
    if self._orchestrator is not None:
        self._orchestrator.invalidate_text_cache(path)
        self._orchestrator.invalidate_before_cache()
    logger.debug("Invalidated asset caches for %s", path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_session.TestInvalidationCascades -v`
Expected: All tests PASS (including existing ones)

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/session.py tests/test_session.py
git commit -m "feat(cache): add invalidate_asset_caches and expand invalidate_script_map"
```

---

### Task 5: Watcher dispatch update

**Files:**
- Modify: `prefab_sentinel/watcher.py:95-97`
- Test: `tests/test_watcher.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_watcher.py` in `TestDispatchChanges`:

```python
def test_prefab_change_calls_invalidate_asset_caches(self) -> None:
    session = MagicMock()
    dispatch_changes(session, {(2, "/project/Assets/Prefabs/Player.prefab")})
    session.invalidate_asset_caches.assert_called_once_with(
        Path("/project/Assets/Prefabs/Player.prefab")
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test python -m unittest tests.test_watcher.TestDispatchChanges.test_prefab_change_calls_invalidate_asset_caches -v`
Expected: FAIL — `invalidate_asset_caches` not called

- [ ] **Step 3: Update watcher dispatch**

In `prefab_sentinel/watcher.py`, replace lines 95-97:

```python
for ap in asset_paths:
    logger.debug("Asset changed: %s — evicting SymbolTree", ap)
    session.invalidate_symbol_tree(ap)
```

with:

```python
for ap in asset_paths:
    logger.debug("Asset changed: %s — invalidating asset caches", ap)
    session.invalidate_asset_caches(ap)
    session.invalidate_symbol_tree(ap)
```

- [ ] **Step 4: Run all watcher tests**

Run: `uv run --extra test python -m unittest tests.test_watcher -v`
Expected: All tests PASS

- [ ] **Step 5: Update existing watcher tests that check `invalidate_symbol_tree` for asset changes**

The existing tests `test_prefab_change_evicts_symbol_tree`, `test_mat_change_evicts_symbol_tree`, `test_unity_scene_change_evicts_symbol_tree` should still pass since `invalidate_symbol_tree` is still called. But verify the new `test_prefab_change_calls_invalidate_asset_caches` passes alongside them.

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/watcher.py tests/test_watcher.py
git commit -m "feat(cache): wire asset change dispatch to invalidate_asset_caches"
```

---

### Task 6: Session resolve_scope

**Files:**
- Modify: `prefab_sentinel/session.py:66-77`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write failing tests**

Add a new test class in `tests/test_session.py`:

```python
class TestResolveScope(unittest.TestCase):
    """resolve_scope returns explicit > session > None."""

    def test_explicit_scope_wins(self) -> None:
        session = ProjectSession()
        session._scope = Path("/project/Assets/A")
        self.assertEqual("Assets/B", session.resolve_scope("Assets/B"))

    def test_session_scope_fallback(self) -> None:
        session = ProjectSession()
        session._scope = Path("/project/Assets/A")
        result = session.resolve_scope(None)
        self.assertEqual(str(Path("/project/Assets/A")), result)

    def test_none_when_no_scope(self) -> None:
        session = ProjectSession()
        self.assertIsNone(session.resolve_scope(None))

    def test_explicit_empty_string_is_not_none(self) -> None:
        session = ProjectSession()
        session._scope = Path("/project/Assets/A")
        # Empty string is explicit (not None)
        self.assertEqual("", session.resolve_scope(""))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test python -m unittest tests.test_session.TestResolveScope -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement resolve_scope**

Add to `ProjectSession` in `prefab_sentinel/session.py` (in the Properties section, after `scope` property):

```python
def resolve_scope(self, explicit_scope: str | None) -> str | None:
    """Return *explicit_scope* if given, else session scope as str."""
    if explicit_scope is not None:
        return explicit_scope
    return str(self._scope) if self._scope is not None else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_session.TestResolveScope -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/session.py tests/test_session.py
git commit -m "feat(session): add resolve_scope for MCP tool scope fallback"
```

---

### Task 7: MCP scope fallback + activate_project diagnostics

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:109-130,261-280,282-301,711-736,738-753`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing tests**

Add a new test class in `tests/test_mcp_server.py`:

The `session` object is closure-scoped inside `create_server()` and not directly accessible. Use `patch.object(ProjectSession, "resolve_scope")` to control scope resolution without needing access to the session instance.

```python
from prefab_sentinel.session import ProjectSession

class TestScopeFallback(unittest.TestCase):
    """MCP tools use session scope when explicit scope is omitted."""

    def test_validate_refs_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {}}
        with (
            patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Resolved"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.validate_refs.return_value = mock_resp
            _run(server.call_tool("validate_refs", {"scope": "Assets/Explicit"}))
            self.assertEqual(
                "Assets/Resolved",
                mock_orch.validate_refs.call_args.kwargs["scope"],
            )

    def test_find_referencing_assets_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_step = MagicMock()
        mock_step.success = True
        mock_step.data = {"usages": [], "usage_count": 0, "truncated_usages": 0}
        with (
            patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Fallback"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            _run(server.call_tool(
                "find_referencing_assets",
                {"asset_or_guid": "abcd1234abcd1234abcd1234abcd1234"},
            ))
            self.assertEqual(
                "Assets/Fallback",
                mock_orch.reference_resolver.where_used.call_args.kwargs["scope"],
            )

    def test_validate_field_rename_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {}}
        with (
            patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Resolved"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.validate_field_rename.return_value = mock_resp
            _run(server.call_tool("validate_field_rename", {
                "script_path_or_guid": "aabb",
                "old_name": "speed",
                "new_name": "velocity",
            }))
            self.assertEqual(
                "Assets/Resolved",
                mock_orch.validate_field_rename.call_args.kwargs["scope"],
            )

    def test_check_field_coverage_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {}}
        with (
            patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Resolved"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.check_field_coverage.return_value = mock_resp
            _run(server.call_tool("check_field_coverage", {"scope": "Assets/Explicit"}))
            self.assertEqual(
                "Assets/Resolved",
                mock_orch.check_field_coverage.call_args.kwargs["scope"],
            )
```

**Note on `validate_refs` and `check_field_coverage`:** These have `scope: str` (required) in their MCP tool signatures. The `resolve_scope` call is applied but is effectively a pass-through since the MCP SDK ensures `scope` is always a non-None string. The fallback to session scope only meaningfully activates for `find_referencing_assets` and `validate_field_rename` (where `scope: str | None`). The `resolve_scope` call is kept on all 4 tools for consistency.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test python -m unittest tests.test_mcp_server.TestScopeFallback -v`
Expected: FAIL

- [ ] **Step 3: Implement scope fallback in MCP tools**

In `prefab_sentinel/mcp_server.py`:

**activate_project** (around line 122-130) — add diagnostics:

```python
result = await session.activate(scope)
return {
    "success": True,
    "severity": "info",
    "code": "SESSION_ACTIVATED",
    "message": f"Project activated with scope: {scope}",
    "data": result,
    "diagnostics": [
        {
            "message": (
                f"Scope '{scope}' will be used as default for: "
                "validate_refs, find_referencing_assets, "
                "validate_field_rename, check_field_coverage."
            ),
            "severity": "info",
        },
    ],
}
```

**find_referencing_assets** (line 274-280) — replace with direct `where_used` call + scope fallback (full replacement in Task 8).

**validate_refs** (line 296-298) — add scope resolution:

```python
orch = session.get_orchestrator()
resolved_scope = session.resolve_scope(scope)
resp = orch.validate_refs(
    scope=resolved_scope,
    details=details,
    max_diagnostics=max_diagnostics,
)
```

**validate_field_rename** (line 729-735) — add scope resolution:

```python
orch = session.get_orchestrator()
resolved_scope = session.resolve_scope(scope)
resp = orch.validate_field_rename(
    script_path_or_guid=script_path_or_guid,
    old_name=old_name,
    new_name=new_name,
    scope=resolved_scope,
)
```

**check_field_coverage** (line 751-752) — add scope resolution:

```python
orch = session.get_orchestrator()
resolved_scope = session.resolve_scope(scope)
resp = orch.check_field_coverage(scope=resolved_scope)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_mcp_server.TestScopeFallback -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add session scope fallback to 4 scope-bearing tools"
```

---

### Task 8: find_referencing_assets direct payload

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:261-280`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_mcp_server.py`:

```python
class TestFindReferencingAssetsDirectPayload(unittest.TestCase):
    """find_referencing_assets returns direct payload, not envelope."""

    def test_returns_matches_array(self) -> None:
        from prefab_sentinel.contracts import Severity, ToolResponse

        server = create_server()
        mock_step = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REF_WHERE_USED",
            message="Found 2 usages",
            data={
                "usages": [
                    {"file": "A.prefab", "line": 10},
                    {"file": "B.prefab", "line": 20},
                ],
                "usage_count": 2,
                "returned_usages": 2,
                "truncated_usages": 0,
                "scanned_files": 5,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            _, result = _run(server.call_tool(
                "find_referencing_assets",
                {"asset_or_guid": "abcd1234abcd1234abcd1234abcd1234"},
            ))

        # Direct payload — no envelope
        self.assertIn("matches", result)
        self.assertEqual(2, len(result["matches"]))
        self.assertEqual("abcd1234abcd1234abcd1234abcd1234", result["target"])
        self.assertFalse(result["metadata"]["truncated"])
        self.assertEqual(2, result["metadata"]["total_count"])
        # No envelope keys
        self.assertNotIn("success", result)
        self.assertNotIn("severity", result)

    def test_truncated_metadata(self) -> None:
        from prefab_sentinel.contracts import Severity, ToolResponse

        server = create_server()
        mock_step = ToolResponse(
            success=True,
            severity=Severity.WARNING,
            code="REF_WHERE_USED",
            message="Truncated",
            data={
                "usages": [{"file": "A.prefab"}],
                "usage_count": 50,
                "returned_usages": 1,
                "truncated_usages": 49,
                "scanned_files": 100,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            _, result = _run(server.call_tool(
                "find_referencing_assets",
                {"asset_or_guid": "x" * 32, "max_results": 1},
            ))

        self.assertTrue(result["metadata"]["truncated"])
        self.assertEqual(50, result["metadata"]["total_count"])

    def test_error_raises_tool_error(self) -> None:
        from prefab_sentinel.contracts import Severity, ToolResponse

        server = create_server()
        mock_step = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="REF_ERR",
            message="Scope not found",
            data={},
            diagnostics=[],
        )
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            with self.assertRaises(Exception) as ctx:
                _run(server.call_tool(
                    "find_referencing_assets",
                    {"asset_or_guid": "x" * 32},
                ))
            self.assertIn("Scope not found", str(ctx.exception))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test python -m unittest tests.test_mcp_server.TestFindReferencingAssetsDirectPayload -v`
Expected: FAIL

- [ ] **Step 3: Implement direct payload**

Replace `find_referencing_assets` in `prefab_sentinel/mcp_server.py` (lines 261-280):

```python
@server.tool()
def find_referencing_assets(
    asset_or_guid: str,
    scope: str | None = None,
    max_results: int = 100,
) -> dict[str, Any]:
    """Find all assets that reference a given asset path or GUID.

    Returns a direct payload with matches array (not an envelope).

    Args:
        asset_or_guid: Asset path or 32-char GUID to search for.
        scope: Directory to restrict search scope.
        max_results: Maximum number of results to return.
    """
    orch = session.get_orchestrator()
    resolved_scope = session.resolve_scope(scope)
    step = orch.reference_resolver.where_used(
        asset_or_guid=asset_or_guid,
        scope=resolved_scope,
        max_usages=max_results,
    )
    if not step.success:
        from mcp.server.fastmcp import ToolError
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_mcp_server.TestFindReferencingAssetsDirectPayload -v`
Expected: 3 tests PASS

- [ ] **Step 5: Update existing test that checks old envelope format**

In `tests/test_mcp_server.py`, update `TestOrchestratorBackedTools.test_find_referencing_assets_delegates` to match the new direct payload format. The test should verify `result["matches"]` instead of `result["success"]`, and check that `reference_resolver.where_used` is called instead of `inspect_where_used`.

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): convert find_referencing_assets to direct payload format

BREAKING CHANGE: Response format changes from envelope to direct payload.
Clients must use result['matches'] instead of result['data']['usages']."
```

---

### Task 9: Documentation updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `docs/ROADMAP_SERENA_FOR_UNITY.md`

- [ ] **Step 1: Update CLAUDE.md API convention**

In `CLAUDE.md`, update the API/error convention section. Move `find_referencing_assets` from the orchestrator category to the reference category:

Change:
```
- **参照系ツール**（`get_unity_symbols`, `find_unity_symbol`）
```
to:
```
- **参照系ツール**（`get_unity_symbols`, `find_unity_symbol`, `find_referencing_assets`）
```

And remove `find_referencing_assets` from the orchestrator line:
```
- **orchestrator 系ツール**（`inspect_wiring`, `inspect_variant` 等）
```

- [ ] **Step 2: Update README.md MCP tool documentation**

Find the MCP tools section in `README.md` and update `find_referencing_assets` response format description to document the direct payload (`matches`, `target`, `metadata`).

- [ ] **Step 3: Update roadmap completion matrix**

In `docs/ROADMAP_SERENA_FOR_UNITY.md`, update section 5 (到達度マトリクス) — set all items to 100%:

```markdown
| Serena の価値 | 現状 | 到達度 | 主要ギャップ |
|---------------|------|--------|-------------|
| シンボルモデル | SymbolTree + 名前パス解決 | 100% | — |
| セマンティックナビ | depth/props/origin 付きクエリ | 100% | — |
| セマンティック編集 | set/add/remove_component (名前→fileID 解決) | 100% | — |
| MCP サーバー | 15 ツール + session 管理 + 直接ペイロード統一 | 100% | — |
| プロジェクトスコープ | activate_project(scope) + scope フォールバック | 100% | — |
| ステートフル | ProjectSession + watchfiles + 細粒度キャッシュ無効化 | 100% | — |
| C# 接続 | field parser + rename/coverage + 継承チェーン | 100% | — |
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md docs/ROADMAP_SERENA_FOR_UNITY.md
git commit -m "docs: update API conventions, README, and roadmap matrix to 100%"
```

---

### Task 10: Regression test + version bump

- [ ] **Step 1: Run full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: All tests PASS (1200+ tests)

- [ ] **Step 2: Fix any regressions**

If tests fail, fix and re-run until all pass.

- [ ] **Step 3: Minor version bump**

Run: `SKIP_BUMP=1 uv run bump-my-version bump minor`

Note: `SKIP_BUMP=1` is set because the bump itself should not trigger the pre-commit patch bump hook.

- [ ] **Step 4: Commit version bump**

The bump-my-version tool auto-commits. Verify with `git log --oneline -1`.

- [ ] **Step 5: Final verification**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: All tests PASS
