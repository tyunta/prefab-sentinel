# MCP Consolidation Phase 2: AI Completeness

Phase 1 で移植した 18 ツールに加え、AI エージェントの作業に必要な残り 3 ツールを MCP に追加する。

## Background

Phase 1 で editor 系 15 + inspection 2 + revert 1 = 18 ツールを追加し、MCP ツール計 33 になった。しかし AI が頻用する `inspect hierarchy`・`patch apply`・`validate runtime` は CLI にしかなく、シェル実行が必要だった。

方針: **AI がやる作業は全て MCP で完結させる。CLI は凍結のまま残す（削除は将来）。**

## Scope

- **3 MCP ツール追加**（inspect_hierarchy + patch_apply + validate_runtime）
- 破壊的変更なし（追加のみ）
- CLI コードは変更しない
- 全ツールは同期関数（orchestrator は同期 API）

## Architecture

```
MCP ツール（mcp_server.py に追加）
  ├── inspect_hierarchy  → orch.inspect_hierarchy()
  ├── patch_apply        → orch.patch_apply()
  └── validate_runtime   → orch.validate_runtime()
```

3 ツールとも orchestrator メソッドをラップし `ToolResponse.to_dict()` でエンベロープ化する。既存の `inspect_wiring` / `set_property` と同パターン。

**挿入位置:**
- `inspect_hierarchy`: 既存の「Inspection tools」セクション内、`validate_structure` の直後。
- `validate_runtime` / `patch_apply`: 新セクション「AI workflow tools」として `return server` の直前。

**Phase 1 との整合:** Phase 1 spec は「CLI は Phase 2 で削除」としていたが、AI 完結に不要な CLI コマンドが多いため削除は将来に延期する。

## 1. inspect_hierarchy

階層ツリー表示。既存 `inspect_materials` / `validate_structure` と同パターン。

```python
@server.tool()
def inspect_hierarchy(
    path: str,
    max_depth: int | None = None,
    show_components: bool = True,
) -> dict[str, Any]:
    """Display the GameObject hierarchy tree of a Unity asset.

    Args:
        path: Path to a .prefab or .unity file.
        max_depth: Maximum tree depth to display (None = unlimited).
        show_components: Show component annotations (default: True).
    """
    orch = session.get_orchestrator()
    resp = orch.inspect_hierarchy(
        target_path=path,
        max_depth=max_depth,
        show_components=show_components,
    )
    return resp.to_dict()
```

**CLI との差異:**
- CLI の `--no-components` フラグ（反転 bool）を MCP では `show_components=True`（正論理）に統一。
- `--format` は CLI 出力形式制御用。MCP では不要（常に dict）。

## 2. validate_runtime

UdonSharp コンパイル + ClientSim 実行検証。orchestrator 直接委譲。

```python
@server.tool()
def validate_runtime(
    scene_path: str,
    profile: str = "default",
    log_file: str | None = None,
    since_timestamp: str | None = None,
    allow_warnings: bool = False,
    max_diagnostics: int = 200,
) -> dict[str, Any]:
    """Run runtime validation: UdonSharp compile + ClientSim execution.

    Args:
        scene_path: Target Unity scene path.
        profile: Runtime profile label for ClientSim execution context.
        log_file: Unity log file path (default: <project>/Logs/Editor.log).
        since_timestamp: Log cursor label for filtering.
        allow_warnings: Treat warning-only findings as pass.
        max_diagnostics: Maximum diagnostics to include (default: 200).
    """
    orch = session.get_orchestrator()
    resp = orch.validate_runtime(
        scene_path=scene_path,
        profile=profile,
        log_file=log_file,
        since_timestamp=since_timestamp,
        allow_warnings=allow_warnings,
        max_diagnostics=max_diagnostics,
    )
    return resp.to_dict()
```

**CLI との差異:** なし。パラメータ名・デフォルト値が完全に一致。

## 3. patch_apply

パッチ計画の検証・適用。AI が構築した plan dict を直接受け取る。

```python
@server.tool()
def patch_apply(
    plan: str,
    confirm: bool = False,
    change_reason: str = "",
    scope: str | None = None,
    runtime_scene: str | None = None,
    runtime_profile: str = "default",
    runtime_log_file: str | None = None,
    runtime_since_timestamp: str | None = None,
    runtime_allow_warnings: bool = False,
    runtime_max_diagnostics: int = 200,
) -> dict[str, Any]:
    """Validate and apply a patch plan to Unity assets.

    Two-phase workflow:
    - confirm=False (default): dry-run validation only.
    - confirm=True: applies changes and runs post-apply checks.

    Args:
        plan: Patch plan as JSON string. Must conform to plan_version "2".
        confirm: Set True to apply (False = dry-run only).
        change_reason: Required when confirm=True. Audit log reason.
        scope: Directory for post-apply reference validation.
        runtime_scene: Scene path for post-apply runtime validation.
        runtime_profile: ClientSim profile for runtime validation.
        runtime_log_file: Unity log file path for runtime validation.
        runtime_since_timestamp: Log cursor for runtime validation.
        runtime_allow_warnings: Allow warnings in runtime validation.
        runtime_max_diagnostics: Max diagnostics for runtime validation.
    """
    import json as _json
    try:
        plan_dict = _json.loads(plan)
    except (ValueError, TypeError) as exc:
        return {
            "success": False, "severity": "error", "code": "INVALID_PLAN_JSON",
            "message": f"Failed to parse plan JSON: {exc}",
            "data": {}, "diagnostics": [],
        }

    orch = session.get_orchestrator()
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
    return resp.to_dict()
```

**設計判断:**
- `plan` は JSON 文字列で受け取る。MCP ツールパラメータは primitive 型が推奨されるため dict ではなく str。ツール内で `json.loads()` する。
- CLI の sha256/signature/attestation/out-report は CI セキュリティ機能 → MCP では省略。
- `plan_sha256` / `plan_signature` は orchestrator に渡さない（`None` デフォルト）。
- `dry_run` は `not confirm` で導出（`set_property` 等と同パターン）。

## Test Plan

### inspect_hierarchy

```python
class TestInspectHierarchyTool(unittest.TestCase):

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"tree": "..."}}
        mock_orch = MagicMock()
        mock_orch.inspect_hierarchy.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool("inspect_hierarchy", {"path": "Assets/A.prefab"}))

        self.assertTrue(result["success"])
        mock_orch.inspect_hierarchy.assert_called_once_with(
            target_path="Assets/A.prefab",
            max_depth=None,
            show_components=True,
        )

    def test_passes_optional_params(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        mock_orch = MagicMock()
        mock_orch.inspect_hierarchy.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _run(server.call_tool("inspect_hierarchy", {
                "path": "Assets/A.prefab", "max_depth": 2, "show_components": False,
            }))

        mock_orch.inspect_hierarchy.assert_called_once_with(
            target_path="Assets/A.prefab", max_depth=2, show_components=False,
        )
```

### validate_runtime

```python
class TestValidateRuntimeTool(unittest.TestCase):

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"steps": []}}
        mock_orch = MagicMock()
        mock_orch.validate_runtime.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool("validate_runtime", {
                "scene_path": "Assets/Scenes/Main.unity",
            }))

        self.assertTrue(result["success"])
        mock_orch.validate_runtime.assert_called_once_with(
            scene_path="Assets/Scenes/Main.unity",
            profile="default",
            log_file=None,
            since_timestamp=None,
            allow_warnings=False,
            max_diagnostics=200,
        )

    def test_passes_all_params(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        mock_orch = MagicMock()
        mock_orch.validate_runtime.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _run(server.call_tool("validate_runtime", {
                "scene_path": "Assets/S.unity",
                "profile": "smoke",
                "log_file": "/tmp/Editor.log",
                "allow_warnings": True,
                "max_diagnostics": 50,
            }))

        mock_orch.validate_runtime.assert_called_once_with(
            scene_path="Assets/S.unity",
            profile="smoke",
            log_file="/tmp/Editor.log",
            since_timestamp=None,
            allow_warnings=True,
            max_diagnostics=50,
        )
```

### patch_apply

```python
class TestPatchApplyTool(unittest.TestCase):

    def test_dry_run_default(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "code": "PATCH_DRY_RUN"}
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = mock_resp

        plan_json = '{"plan_version": "2", "resources": [], "ops": []}'
        server = create_server()
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(server.call_tool("patch_apply", {"plan": plan_json}))

        self.assertTrue(result["success"])
        mock_orch.patch_apply.assert_called_once_with(
            plan={"plan_version": "2", "resources": [], "ops": []},
            dry_run=True,
            confirm=False,
            change_reason=None,
            scope=None,
            runtime_scene=None,
            runtime_profile="default",
            runtime_log_file=None,
            runtime_since_timestamp=None,
            runtime_allow_warnings=False,
            runtime_max_diagnostics=200,
        )

    def test_confirm_mode(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "code": "PATCH_APPLIED"}
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = mock_resp

        plan_json = '{"plan_version": "2", "resources": [], "ops": []}'
        server = create_server()
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _run(server.call_tool("patch_apply", {
                "plan": plan_json, "confirm": True, "change_reason": "Fix color",
            }))

        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])
        self.assertEqual("Fix color", call_kwargs["change_reason"])

    def test_invalid_json_returns_error(self) -> None:
        server = create_server()
        _, result = _run(server.call_tool("patch_apply", {"plan": "not json"}))
        self.assertFalse(result["success"])
        self.assertEqual("INVALID_PLAN_JSON", result["code"])
        self.assertEqual("error", result["severity"])
        self.assertIn("parse", result["message"])

    def test_empty_change_reason_becomes_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _run(server.call_tool("patch_apply", {
                "plan": '{"plan_version": "2"}', "change_reason": "",
            }))

        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertIsNone(call_kwargs["change_reason"])
```

### test_all_tools_registered 更新

```python
expected = {
    # ... existing 33 tools ...
    # New 3 tools
    "inspect_hierarchy", "validate_runtime", "patch_apply",
}
self.assertEqual(expected, tool_names)

def test_tool_count(self) -> None:
    ...
    self.assertEqual(36, len(tools))
```

## File Change Summary

| ファイル | 変更内容 |
|---------|---------|
| `mcp_server.py` | 3 ツール追加 |
| `tests/test_mcp_server.py` | 3 テストクラス追加 + registration 更新 |
| `README.md` | MCP ツール一覧に 3 ツール追加 |

## Version

ツール追加のみ（破壊的変更なし）。patch バンプ（pre-commit hook で自動）。
