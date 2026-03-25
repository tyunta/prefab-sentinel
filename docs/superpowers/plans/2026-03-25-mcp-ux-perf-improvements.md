# MCP UX & Performance Improvements Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `patch_apply` plan parsing, add class name resolution to `list_serialized_fields`, and speed up C# field operations by sharing caches.

**Architecture:** Four fixes — (1) lenient plan parsing with better errors, (2) class name → .cs path reverse lookup in `resolve_script_fields`, (3) `_collect_scope_files` caching in `ReferenceResolverService`, (4) orchestrator C# field methods use cached text reads instead of direct `decode_text_file`. Tasks 1-3 are independent. **Task 4 depends on Task 3** (uses `collect_scope_files` API).

**Tech Stack:** Python 3.12, unittest

---

## File Structure

| File | Responsibility |
|------|---------------|
| `prefab_sentinel/patch_plan.py` | Lenient `plan_version` parsing, better error messages |
| `prefab_sentinel/csharp_fields.py` | Class name → path reverse lookup in `resolve_script_fields` |
| `prefab_sentinel/services/reference_resolver.py` | `_scope_files_cache` + public `collect_scope_files` method |
| `prefab_sentinel/orchestrator.py` | Wire `validate_field_rename` / `check_field_coverage` through cached reads |
| `tests/test_patch_plan.py` | Tests for lenient parsing |
| `tests/test_csharp_fields.py` | Tests for class name resolution |
| `tests/test_services.py` | Tests for scope files caching |

---

### Task 1: Lenient `patch_apply` plan parsing + better errors (B1 + U4)

**Files:**
- Modify: `prefab_sentinel/patch_plan.py:88-97`
- Modify: `prefab_sentinel/mcp_server.py:1119-1145` (add ValueError handler)
- Test: `tests/test_patch_plan.py`

Two root causes: (a) `normalize_patch_plan` rejects `plan_version: "2"` (string) and `version` alias. (b) MCP `patch_apply` tool lacks try-catch around `orch.patch_apply()` — `ValueError` from `normalize_patch_plan` propagates as unhandled `ToolError` instead of structured error response.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_patch_plan.py` class `NormalizePatchPlanTests`:

```python
def test_string_plan_version_accepted(self) -> None:
    plan = _v2_plan()
    plan["plan_version"] = "2"
    result = normalize_patch_plan(plan)
    self.assertEqual(result["plan_version"], PLAN_VERSION)

def test_version_alias_accepted(self) -> None:
    plan = _v2_plan()
    del plan["plan_version"]
    plan["version"] = 2
    result = normalize_patch_plan(plan)
    self.assertEqual(result["plan_version"], PLAN_VERSION)

def test_version_alias_string_accepted(self) -> None:
    plan = _v2_plan()
    del plan["plan_version"]
    plan["version"] = "2"
    result = normalize_patch_plan(plan)
    self.assertEqual(result["plan_version"], PLAN_VERSION)

def test_wrong_version_error_message_includes_received_value(self) -> None:
    with self.assertRaises(ValueError) as ctx:
        normalize_patch_plan({"plan_version": 99})
    self.assertIn("99", str(ctx.exception))

def test_non_numeric_plan_version_raises(self) -> None:
    with self.assertRaises(ValueError) as ctx:
        normalize_patch_plan({"plan_version": "abc"})
    self.assertIn("abc", str(ctx.exception))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test python -m unittest tests.test_patch_plan.NormalizePatchPlanTests.test_string_plan_version_accepted tests.test_patch_plan.NormalizePatchPlanTests.test_version_alias_accepted tests.test_patch_plan.NormalizePatchPlanTests.test_version_alias_string_accepted tests.test_patch_plan.NormalizePatchPlanTests.test_wrong_version_error_message_includes_received_value tests.test_patch_plan.NormalizePatchPlanTests.test_non_numeric_plan_version_raises -v`
Expected: All 5 FAIL

- [ ] **Step 3: Implement lenient parsing**

In `prefab_sentinel/patch_plan.py`, replace the `if "plan_version" not in payload:` block (lines 92-116) with:

```python
    raw_version = payload.get("plan_version", payload.get("version"))
    if raw_version is None:
        normalized = _normalize_v1_plan(payload)
    else:
        try:
            plan_version = int(raw_version)
        except (TypeError, ValueError):
            raise _error("plan_version", f"must be an integer, got {raw_version!r}.")
        if plan_version != PLAN_VERSION:
            raise _error("plan_version", f"must equal {PLAN_VERSION}, got {plan_version}.")

        resources = payload.get("resources")
        if not isinstance(resources, list) or not resources:
            raise _error("resources", "must be a non-empty array.")

        ops = payload.get("ops")
        if not isinstance(ops, list):
            raise _error("ops", "must be an array.")

        postconditions = payload.get("postconditions", [])
        if not isinstance(postconditions, list):
            raise _error("postconditions", "must be an array when provided.")

        normalized = {
            "plan_version": PLAN_VERSION,
            "resources": [_normalize_resource(resource, index) for index, resource in enumerate(resources)],
            "ops": [deepcopy(op) for op in ops],
            "postconditions": [deepcopy(postcondition) for postcondition in postconditions],
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_patch_plan.NormalizePatchPlanTests -v`
Expected: ALL PASS

- [ ] **Step 5: Add ValueError handler to MCP patch_apply tool**

In `prefab_sentinel/mcp_server.py`, wrap the `orch.patch_apply()` call (lines 1129-1144) with a ValueError handler:

```python
        try:
            resp = orch.patch_apply(
                plan=plan_dict,
                dry_run=not confirm,
                confirm=confirm,
                plan_sha256=None,
                plan_signature=None,
                change_reason=change_reason or None,
                scope=scope,
                runtime_scene=runtime_scene,
                runtime_profile=runtime_profile,
                runtime_log_file=runtime_log_file,
                runtime_since_timestamp=runtime_since_timestamp,
                runtime_allow_warnings=runtime_allow_warnings,
                runtime_max_diagnostics=runtime_max_diagnostics,
            )
        except ValueError as exc:
            return {
                "success": False, "severity": "error",
                "code": "INVALID_PLAN_SCHEMA",
                "message": f"Plan validation failed: {exc}",
                "data": {}, "diagnostics": [],
            }
        return resp.to_dict()
```

- [ ] **Step 6: Run full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/patch_plan.py prefab_sentinel/mcp_server.py tests/test_patch_plan.py
git commit -m "fix: lenient patch_apply plan_version parsing + ValueError handler in MCP tool"
```

---

### Task 2: Class name resolution in `resolve_script_fields` (B2 + U2)

**Files:**
- Modify: `prefab_sentinel/csharp_fields.py:329-388`
- Test: `tests/test_csharp_fields.py`

Currently `resolve_script_fields` only accepts file path or GUID. Add a class name lookup step: after file path resolution fails, check if the identifier matches a `.cs` file stem in the GUID index.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_csharp_fields.py` class `TestResolveScriptFields`:

```python
def test_resolve_by_class_name(self) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assets = root / "Assets"
        scripts = assets / "Scripts"
        scripts.mkdir(parents=True)

        cs = scripts / "MyComponent.cs"
        cs.write_text("public float speed;", encoding="utf-8")
        meta = Path(str(cs) + ".meta")
        meta.write_text(
            "fileFormatVersion: 2\nguid: aabb1122ccdd3344eeff5566aabb7788\n",
            encoding="utf-8",
        )

        guid, path, fields = resolve_script_fields(
            "MyComponent", project_root=root
        )

    self.assertEqual("aabb1122ccdd3344eeff5566aabb7788", guid)
    self.assertEqual(cs, path)
    self.assertEqual(1, len(fields))

def test_resolve_by_class_name_ambiguous_raises(self) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assets = root / "Assets"
        dir_a = assets / "A"
        dir_b = assets / "B"
        dir_a.mkdir(parents=True)
        dir_b.mkdir(parents=True)

        for d, guid_hex in [(dir_a, "aa"), (dir_b, "bb")]:
            cs = d / "Dup.cs"
            cs.write_text("public int x;", encoding="utf-8")
            meta = Path(str(cs) + ".meta")
            meta.write_text(
                f"fileFormatVersion: 2\nguid: {guid_hex * 16}\n",
                encoding="utf-8",
            )

        with self.assertRaises(FileNotFoundError) as ctx:
            resolve_script_fields("Dup", project_root=root)
        self.assertIn("multiple", str(ctx.exception).lower())

def test_resolve_by_class_name_no_project_root_raises(self) -> None:
    with self.assertRaises(FileNotFoundError):
        resolve_script_fields("SomeClass")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test python -m unittest tests.test_csharp_fields.TestResolveScriptFields.test_resolve_by_class_name tests.test_csharp_fields.TestResolveScriptFields.test_resolve_by_class_name_ambiguous_raises tests.test_csharp_fields.TestResolveScriptFields.test_resolve_by_class_name_no_project_root_raises -v`
Expected: FAIL

- [ ] **Step 3: Implement class name resolution**

In `prefab_sentinel/csharp_fields.py`, replace the "Treat as file path" section (lines 372-388) of `resolve_script_fields`:

```python
    # Treat as file path
    cs_path = Path(to_wsl_path(identifier))
    if not cs_path.is_file():
        # Try class name resolution via GUID index stem matching
        if project_root is not None:
            guid_index = collect_project_guid_index(
                project_root, include_package_cache=False
            )
            stem_matches: list[tuple[str, Path]] = [
                (g, p) for g, p in guid_index.items()
                if p.suffix == ".cs" and p.stem == identifier
            ]
            if len(stem_matches) == 1:
                matched_guid, matched_path = stem_matches[0]
                source = matched_path.read_text(encoding="utf-8-sig")
                fields = parse_serialized_fields(source)
                return matched_guid, matched_path, fields
            if len(stem_matches) > 1:
                paths = ", ".join(str(p) for _, p in stem_matches)
                msg = f"Multiple scripts match class name '{identifier}': {paths}"
                raise FileNotFoundError(msg)
        msg = f"Script file not found: {identifier}"
        raise FileNotFoundError(msg)

    # Find GUID from .meta file
    meta_path = Path(str(cs_path) + ".meta")
    guid = ""
    if meta_path.is_file():
        meta_text = meta_path.read_text(encoding="utf-8")
        guid_match = re.search(r"guid:\s*([0-9a-fA-F]{32})", meta_text)
        if guid_match:
            guid = guid_match.group(1).lower()

    source = cs_path.read_text(encoding="utf-8-sig")
    fields = parse_serialized_fields(source)
    return guid, cs_path, fields
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_csharp_fields.TestResolveScriptFields -v`
Expected: ALL PASS

- [ ] **Step 5: Update MCP docstring**

In `prefab_sentinel/mcp_server.py`, update the `list_serialized_fields` docstring `script_or_guid` arg:

```python
            script_or_guid: .cs file path, class name (e.g. "NadeSharePuppetSpec"),
                or 32-char GUID string. Class name resolution requires an active project.
```

Also update `validate_field_rename` docstring similarly.

- [ ] **Step 6: Update README if needed**

Check if `README.md` documents `list_serialized_fields` / `validate_field_rename` input formats. If so, add "class name" to the accepted inputs list.

- [ ] **Step 7: Run full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
git add prefab_sentinel/csharp_fields.py prefab_sentinel/mcp_server.py tests/test_csharp_fields.py
git commit -m "feat: resolve class name to .cs path in list_serialized_fields / validate_field_rename"
```

---

### Task 3: Cache `_collect_scope_files` in `ReferenceResolverService` (F1 partial)

**Files:**
- Modify: `prefab_sentinel/services/reference_resolver.py:38-43,160-191`
- Test: `tests/test_services.py`

`_collect_scope_files` does `os.walk` on every call (~4.65s on WSL2 9P). Add a dict cache keyed by `(scope_path, exclude_patterns)`. Also expose a public method for orchestrator use.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_services.py` class `ReferenceResolverServiceTests`:

```python
def test_collect_scope_files_cached(self) -> None:
    """Second call returns same list without re-walking."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assets = root / "Assets"
        assets.mkdir()
        prefab = assets / "Test.prefab"
        prefab.write_text("%YAML 1.1\n", encoding="utf-8")

        service = ReferenceResolverService(project_root=root)
        result1 = service.collect_scope_files(assets)
        # Mutate filesystem — cached result should NOT reflect the change
        (assets / "New.prefab").write_text("%YAML 1.1\n", encoding="utf-8")
        result2 = service.collect_scope_files(assets)
        self.assertEqual(result1, result2)  # Same cached list

def test_collect_scope_files_invalidated(self) -> None:
    """After invalidation, re-walk picks up new files."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assets = root / "Assets"
        assets.mkdir()
        prefab = assets / "Test.prefab"
        prefab.write_text("%YAML 1.1\n", encoding="utf-8")

        service = ReferenceResolverService(project_root=root)
        result1 = service.collect_scope_files(assets)
        (assets / "New.prefab").write_text("%YAML 1.1\n", encoding="utf-8")
        service.invalidate_scope_files_cache()
        result2 = service.collect_scope_files(assets)
        self.assertEqual(len(result1) + 1, len(result2))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test python -m unittest tests.test_services.ReferenceResolverServiceTests.test_collect_scope_files_cached tests.test_services.ReferenceResolverServiceTests.test_collect_scope_files_invalidated -v`
Expected: AttributeError (method not found)

- [ ] **Step 3: Implement cached scope files**

In `prefab_sentinel/services/reference_resolver.py`:

1. Add `_scope_files_cache` to `__init__` (line 43):
```python
self._scope_files_cache: dict[tuple[str, tuple[str, ...]], list[Path]] = {}
```

2. Add public method after `invalidate_guid_index` (line 58):
```python
def invalidate_scope_files_cache(self) -> None:
    """Clear the scope files cache."""
    self._scope_files_cache.clear()

def collect_scope_files(
    self,
    scope_path: Path,
    exclude_patterns: tuple[str, ...] = (),
) -> list[Path]:
    """Return cached scope files, populating on first call."""
    key = (str(scope_path), exclude_patterns)
    cached = self._scope_files_cache.get(key)
    if cached is not None:
        return cached
    files = self._collect_scope_files(scope_path, exclude_patterns)
    self._scope_files_cache[key] = files
    return files
```

3. Add invalidation call in `invalidate_text_cache` when `path is None` (line 50):
```python
self._scope_files_cache.clear()
```

4. Update `scan_broken_references` and `where_used` to use `self.collect_scope_files()` instead of `self._collect_scope_files()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_services.ReferenceResolverServiceTests -v`
Expected: ALL PASS

- [ ] **Step 5: Wire session invalidation**

In `prefab_sentinel/orchestrator.py`, add delegation:
```python
def invalidate_scope_files_cache(self) -> None:
    """Delegate scope files cache invalidation to reference resolver."""
    self.reference_resolver.invalidate_scope_files_cache()
```

In `prefab_sentinel/session.py` `invalidate_asset_caches()`, add:
```python
if self._orchestrator:
    self._orchestrator.invalidate_scope_files_cache()
```

- [ ] **Step 6: Run full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/services/reference_resolver.py prefab_sentinel/orchestrator.py prefab_sentinel/session.py tests/test_services.py
git commit -m "perf: cache _collect_scope_files results in ReferenceResolverService"
```

---

### Task 4: Wire orchestrator C# field ops through cached reads (F1-F2 + F3)

**Depends on:** Task 3 (uses `collect_scope_files` and `preload_texts` public APIs)

**Files:**
- Modify: `prefab_sentinel/services/reference_resolver.py` (make `_preload_texts` / `_read_text` public)
- Modify: `prefab_sentinel/orchestrator.py:303-557`
- Test: `tests/test_services.py`

`validate_field_rename` and `check_field_coverage` call `_iter_yaml_files` + `decode_text_file` directly, bypassing all caches. Wire them through `reference_resolver.collect_scope_files()` and `reference_resolver.preload_texts()` / `reference_resolver.read_text()`.

**Behavioral note:** `collect_scope_files` uses `is_unity_text_asset` which includes more suffixes (`.mat`, `.anim`, etc.) than `_iter_yaml_files` (`GAMEOBJECT_BEARING_SUFFIXES`). This is an intentional correctness improvement — those file types can contain MonoBehaviour references.

- [ ] **Step 1: Make `_preload_texts` and `_read_text` public**

In `prefab_sentinel/services/reference_resolver.py`, rename:
- `_read_text` → `read_text` (line 71)
- `_read_text_uncached` → `_read_text_uncached` (keep private, internal only)
- `_preload_texts` → `preload_texts` (line 91)

Update all internal callers (`scan_broken_references`, `where_used`, `_local_ids`, etc.) to use the new names.

- [ ] **Step 2: Write test for cached reads in orchestrator**

Add to `tests/test_services.py` in `ReferenceResolverServiceTests`:

```python
def test_preload_and_read_populates_cache(self) -> None:
    """preload_texts + read_text uses _text_cache."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assets = root / "Assets"
        assets.mkdir()
        prefab = assets / "Test.prefab"
        prefab.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: A\n", encoding="utf-8")

        service = ReferenceResolverService(project_root=root)
        service.preload_texts([prefab])
        # Cache should now be populated
        self.assertIn(prefab, service._text_cache)
        # read_text should return cached value without re-reading
        text = service.read_text(prefab)
        self.assertIn("m_Name: A", text)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run --extra test python -m unittest tests.test_services.ReferenceResolverServiceTests.test_preload_and_read_populates_cache -v`
Expected: PASS (once Step 1 is done, this tests the renamed API)

- [ ] **Step 4: Refactor `validate_field_rename` to use cached reads**

In `prefab_sentinel/orchestrator.py`, method `validate_field_rename` (lines 381-419):

Replace:
```python
        if scan_guids:
            for yaml_path in _iter_yaml_files(scope_path):
                try:
                    text = decode_text_file(yaml_path)
                except (OSError, UnicodeDecodeError):
                    continue
                if text is None:
                    continue
```

With:
```python
        if scan_guids:
            yaml_files = self.reference_resolver.collect_scope_files(scope_path)
            self.reference_resolver.preload_texts(yaml_files)
            for yaml_path in yaml_files:
                text = self.reference_resolver.read_text(yaml_path)
                if text is None:
                    continue
```

- [ ] **Step 5: Refactor `check_field_coverage` to use cached reads + shared GUID index**

In `prefab_sentinel/orchestrator.py`, method `check_field_coverage`:

**5a.** Replace YAML file iteration (lines 480-487):
```python
        for yaml_path in _iter_yaml_files(scope_path):
            try:
                text = decode_text_file(yaml_path)
            except (OSError, UnicodeDecodeError):
                continue
            if text is None:
                continue
```
With:
```python
        yaml_files = self.reference_resolver.collect_scope_files(scope_path)
        self.reference_resolver.preload_texts(yaml_files)
        for yaml_path in yaml_files:
            text = self.reference_resolver.read_text(yaml_path)
            if text is None:
                continue
```

**5b.** Replace 3× GUID index rebuild (line 464-471):

The investigation found `check_field_coverage` rebuilds the GUID index **3 times** — once directly (line 464) and twice inside `build_field_map()` / `build_class_name_index()`. Fix by passing a shared `_guid_index` parameter:

```python
        # BEFORE (3 separate GUID index builds):
        guid_index = collect_project_guid_index(project_root, include_package_cache=False)
        _field_map = build_field_map(project_root)
        _class_index = build_class_name_index(project_root)

        # AFTER (1 cached lookup, shared):
        guid_index = self.reference_resolver._guid_map()
        _field_map = build_field_map(project_root, _guid_index=guid_index)
        _class_index = build_class_name_index(project_root, _guid_index=guid_index)
```

This requires adding an optional `_guid_index` parameter to `build_field_map` and `build_class_name_index` in `csharp_fields.py`:

```python
def build_field_map(
    project_root: Path,
    _guid_index: dict[str, Path] | None = None,
) -> dict[str, list[CSharpField]]:
    guid_index = _guid_index or collect_project_guid_index(project_root, include_package_cache=False)
    # ... rest unchanged
```

Same pattern for `build_class_name_index`.

- [ ] **Step 6: Remove dead `_iter_yaml_files` if unused**

After the refactor, `_iter_yaml_files` is only used by `validate_field_rename` and `check_field_coverage` (the two methods just changed). Grep for other callers. If none, remove it and the `GAMEOBJECT_BEARING_SUFFIXES` import (if no longer used elsewhere in orchestrator.py).

- [ ] **Step 7: Run full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
git add prefab_sentinel/services/reference_resolver.py prefab_sentinel/orchestrator.py tests/test_services.py
git commit -m "perf: wire validate_field_rename / check_field_coverage through cached text reads"
```
