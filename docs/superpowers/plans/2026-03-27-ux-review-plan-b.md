# UX Review Plan B: Infrastructure Improvements

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Bridge auto-deploy detection + `deploy_bridge` tool, and `validate_all_wiring` for batch null-reference scanning across scope.

**Architecture:** Python-side changes only. `session.py` gets Bridge version detection, `orchestrator.py` gets wiring batch scan, `mcp_server.py` gets 2 new tools. No C# changes.

**Tech Stack:** Python 3.11+ (FastMCP, shutil, re)

**Spec:** `docs/superpowers/specs/2026-03-27-ux-review-improvements-design.md` (Spec B section)

---

## File Structure

### Modified Files

| File | Responsibility |
|------|---------------|
| `prefab_sentinel/session.py` | `detect_bridge_version()`, `activate()` に Bridge 診断追加 |
| `prefab_sentinel/orchestrator.py` | `validate_all_wiring()` メソッド追加 |
| `prefab_sentinel/mcp_server.py` | `deploy_bridge`, `validate_all_wiring` ツール追加 |
| `tests/test_session.py` | Bridge バージョン検出テスト |
| `tests/test_orchestrator.py` | validate_all_wiring テスト |
| `tests/test_mcp_server.py` | ツール登録テスト (60 → 62) |
| `README.md` | ツールテーブル更新 |

---

## Task 1: session.py — Bridge version detection

**Files:**
- Modify: `prefab_sentinel/session.py`
- Modify: `tests/test_session.py`

- [ ] **Step 1: Add detect_bridge_version to session.py**

Add import at top:

```python
import re
```

Add method to `ProjectSession` class, after `script_name_map` property:

```python
    _BRIDGE_VERSION_RE = re.compile(r'BridgeVersion\s*=\s*"([^"]+)"')

    def detect_bridge_version(self) -> str | None:
        """Detect the BridgeVersion from the Unity project's Editor bridge files.

        Searches for PrefabSentinel.UnityEditorControlBridge.cs in the project's
        Assets/Editor/ directory tree and extracts the BridgeVersion constant.
        Returns None if not found.
        """
        if self._project_root is None:
            return None
        editor_dir = self._project_root / "Assets"
        for cs_file in editor_dir.rglob("PrefabSentinel.UnityEditorControlBridge.cs"):
            try:
                text = cs_file.read_text(encoding="utf-8-sig", errors="replace")
                m = self._BRIDGE_VERSION_RE.search(text)
                if m:
                    return m.group(1)
            except OSError:
                continue
        return None
```

- [ ] **Step 2: Add test for detect_bridge_version**

In `tests/test_session.py`, add:

```python
class TestBridgeVersionDetection(unittest.TestCase):
    def test_detects_version_from_cs_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets" / "Editor" / "PrefabSentinel").mkdir(parents=True)
            cs = root / "Assets" / "Editor" / "PrefabSentinel" / "PrefabSentinel.UnityEditorControlBridge.cs"
            cs.write_text('public const string BridgeVersion = "1.2.3";', encoding="utf-8")
            session = ProjectSession(project_root=root)
            self.assertEqual("1.2.3", session.detect_bridge_version())

    def test_returns_none_when_no_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").mkdir()
            session = ProjectSession(project_root=root)
            self.assertIsNone(session.detect_bridge_version())

    def test_returns_none_when_no_project_root(self) -> None:
        session = ProjectSession()
        self.assertIsNone(session.detect_bridge_version())
```

- [ ] **Step 3: Run tests**

Run: `uv run python -m unittest tests.test_session.TestBridgeVersionDetection -v`
Expected: 3 tests PASS

- [ ] **Step 4: Commit**

```bash
git add prefab_sentinel/session.py tests/test_session.py
git commit -m "feat(session): detect Bridge version from Unity project"
```

---

## Task 2: session.py — activate() Bridge version diagnostics

**Files:**
- Modify: `prefab_sentinel/session.py`
- Modify: `prefab_sentinel/mcp_server.py` (activate_project response)

- [ ] **Step 1: Add bridge version check to activate()**

In `session.py`, add a method that returns diagnostics:

```python
    def check_bridge_version(self) -> dict[str, Any] | None:
        """Check if the Unity project's Bridge version matches the plugin version.

        Returns a diagnostic dict if mismatch detected, None if OK or not detectable.
        """
        detected = self.detect_bridge_version()
        if detected is None:
            return {
                "severity": "warning",
                "code": "BRIDGE_NOT_FOUND",
                "message": "Bridge C# files not found in project. "
                "Deploy with deploy_bridge tool or copy tools/unity/*.cs to Assets/Editor/PrefabSentinel/",
            }
        from prefab_sentinel import __version__ as plugin_version

        if detected != plugin_version:
            return {
                "severity": "warning",
                "code": "BRIDGE_VERSION_MISMATCH",
                "message": f"Bridge version {detected}, plugin version {plugin_version}. "
                "Use deploy_bridge tool to update.",
                "data": {
                    "bridge_version": detected,
                    "plugin_version": plugin_version,
                    "bridge_update_available": True,
                },
            }
        return None
```

- [ ] **Step 2: Wire into activate_project MCP tool**

In `prefab_sentinel/mcp_server.py`, in the `activate_project` function, after `session.activate(scope)` call, add bridge version check to diagnostics:

Find the line that builds the response dict after `session.activate()`. Add:

```python
        bridge_diag = session.check_bridge_version()
        diagnostics = list(result.get("diagnostics", []))
        if bridge_diag:
            diagnostics.append(bridge_diag)
        result["diagnostics"] = diagnostics
```

(Exact insertion depends on current activate_project structure — read and adapt.)

- [ ] **Step 3: Commit**

```bash
git add prefab_sentinel/session.py prefab_sentinel/mcp_server.py
git commit -m "feat(session): bridge version mismatch diagnostics in activate_project"
```

---

## Task 3: mcp_server.py — deploy_bridge tool

**Files:**
- Modify: `prefab_sentinel/mcp_server.py`

- [ ] **Step 1: Add deploy_bridge tool**

After the `activate_project` / `get_project_status` tools section:

```python
    @server.tool()
    def deploy_bridge(
        target_dir: str = "",
    ) -> dict[str, Any]:
        """Deploy or update Bridge C# files to the Unity project.

        Copies tools/unity/*.cs from prefab-sentinel to the target directory.
        Triggers editor_refresh after copying to reload assets.

        Args:
            target_dir: Target directory in Unity project. Default: Assets/Editor/PrefabSentinel/
        """
        import shutil
        from pathlib import Path as _Path

        project_root = session.project_root
        if project_root is None:
            return {
                "success": False,
                "severity": "error",
                "code": "DEPLOY_NO_PROJECT",
                "message": "No project activated. Call activate_project first.",
                "data": {},
                "diagnostics": [],
            }

        if not target_dir:
            target_dir = str(project_root / "Assets" / "Editor" / "PrefabSentinel")

        target_path = _Path(target_dir)
        target_path.mkdir(parents=True, exist_ok=True)

        # Find plugin's tools/unity/ directory
        plugin_tools = _Path(__file__).parent.parent / "tools" / "unity"
        if not plugin_tools.is_dir():
            # Fallback: check installed package location
            import importlib.resources

            try:
                pkg_ref = importlib.resources.files("prefab_sentinel").joinpath(
                    "../tools/unity"
                )
                plugin_tools = _Path(str(pkg_ref))
            except Exception:
                pass

        if not plugin_tools.is_dir():
            return {
                "success": False,
                "severity": "error",
                "code": "DEPLOY_SOURCE_NOT_FOUND",
                "message": "Bridge source directory (tools/unity/) not found.",
                "data": {},
                "diagnostics": [],
            }

        old_version = session.detect_bridge_version()
        copied_files: list[str] = []

        for cs_file in sorted(plugin_tools.glob("*.cs")):
            dest = target_path / cs_file.name
            shutil.copy2(cs_file, dest)
            copied_files.append(cs_file.name)

        new_version = session.detect_bridge_version()

        # Trigger asset refresh
        try:
            send_action(action="refresh_asset_database")
        except Exception:
            pass  # Best-effort refresh

        return {
            "success": True,
            "severity": "info",
            "code": "DEPLOY_OK",
            "message": f"Deployed {len(copied_files)} files to {target_dir}",
            "data": {
                "copied_files": copied_files,
                "old_version": old_version,
                "new_version": new_version,
                "target_dir": target_dir,
            },
            "diagnostics": [],
        }
```

- [ ] **Step 2: Commit**

```bash
git add prefab_sentinel/mcp_server.py
git commit -m "feat(mcp): deploy_bridge tool for auto-updating Unity Bridge files"
```

---

## Task 4: orchestrator.py — validate_all_wiring

**Files:**
- Modify: `prefab_sentinel/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

- [ ] **Step 1: Add validate_all_wiring to orchestrator**

In `Phase1Orchestrator` class, add:

```python
    def validate_all_wiring(
        self,
        *,
        target_path: str = "",
    ) -> dict[str, Any]:
        """Run inspect_wiring on all .prefab/.unity files in scope (or a single file).

        Returns aggregated null-reference summary.
        """
        from prefab_sentinel.contracts import success_response

        if target_path:
            paths = [Path(target_path)]
        else:
            paths = sorted(self._collect_scope_files())

        asset_paths = [
            p for p in paths if p.suffix in (".prefab", ".unity")
        ]

        if not asset_paths:
            return success_response(
                code="VALIDATE_WIRING_EMPTY",
                message="No .prefab or .unity files found in scope.",
                data={"files_scanned": 0, "total_components": 0, "total_null_refs": 0},
            )

        total_components = 0
        total_null_refs = 0
        null_refs_by_file: list[dict[str, Any]] = []

        for p in asset_paths:
            try:
                result = self.inspect_wiring(target_path=str(p))
                if not result.get("success", False):
                    continue
                components = result.get("data", {}).get("components", [])
                comp_count = len(components)
                null_count = sum(
                    1 for c in components
                    for _fname in (c.get("null_field_names") or [])
                )
                total_components += comp_count
                total_null_refs += null_count
                if null_count > 0:
                    null_refs_by_file.append({
                        "file": str(p),
                        "null_refs": null_count,
                        "components": comp_count,
                    })
            except Exception:
                continue

        return success_response(
            code="VALIDATE_WIRING_OK",
            message=f"Scanned {len(asset_paths)} files: "
            f"{total_components} components, {total_null_refs} null references",
            data={
                "files_scanned": len(asset_paths),
                "total_components": total_components,
                "total_null_refs": total_null_refs,
                "null_refs_by_file": null_refs_by_file,
            },
        )
```

- [ ] **Step 2: Add test**

In `tests/test_orchestrator.py`, add:

```python
class TestValidateAllWiring(unittest.TestCase):
    def test_empty_scope_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orch = Phase1Orchestrator(scope=Path(tmp))
            result = orch.validate_all_wiring()
            self.assertTrue(result["success"])
            self.assertEqual(0, result["data"]["files_scanned"])
```

- [ ] **Step 3: Run test**

Run: `uv run python -m unittest tests.test_orchestrator.TestValidateAllWiring -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add prefab_sentinel/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): validate_all_wiring for batch null-reference scanning"
```

---

## Task 5: MCP tools + test registration

**Files:**
- Modify: `prefab_sentinel/mcp_server.py`
- Modify: `tests/test_mcp_server.py`
- Modify: `tests/test_editor_bridge.py`

- [ ] **Step 1: Add validate_all_wiring MCP tool**

In `prefab_sentinel/mcp_server.py`, after the inspection tools section:

```python
    @server.tool()
    def validate_all_wiring(
        asset_path: str = "",
    ) -> dict[str, Any]:
        """Scan all .prefab/.unity files in scope for null references.

        Aggregates inspect_wiring results across the entire scope (or a single file).
        Returns a summary with total component count, null reference count,
        and per-file breakdown.

        Args:
            asset_path: Single .unity/.prefab file to scan. Empty = scan entire scope.
        """
        orch = session.get_orchestrator()
        return orch.validate_all_wiring(target_path=asset_path)
```

- [ ] **Step 2: Update test_mcp_server.py**

Add `"deploy_bridge"` and `"validate_all_wiring"` to expected set.
Change count: `self.assertEqual(62, len(tools))`

- [ ] **Step 3: Run tests**

Run: `uv run python -m unittest tests.test_editor_bridge.TestSupportedActions.test_all_actions_present -v`
Expected: PASS

Run: `python3 -m compileall prefab_sentinel/mcp_server.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): deploy_bridge and validate_all_wiring tools (62 tools)"
```

---

## Task 6: Verification + README

- [ ] **Step 1: Full test suite**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: all tests pass.

- [ ] **Step 2: Verify MCP tool count**

Run: `uv run --extra mcp python -c "from prefab_sentinel.mcp_server import create_server; s = create_server(); print(len(s._tool_manager._tools))"`
Expected: 62

- [ ] **Step 3: Update README**

Add 2 tools:

```
| `deploy_bridge` | Unity プロジェクトの Bridge C# ファイルを自動更新 |
| `validate_all_wiring` | スコープ内の全 .prefab/.unity の null 参照を一括スキャン |
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add deploy_bridge and validate_all_wiring to README"
```
