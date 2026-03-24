# MCP Consolidation Design — Phase 1: Feature Migration

CLI 専用機能を MCP ツールとして移植し、AI エージェントが全機能を MCP 経由で利用可能にする。

## Background

prefab-sentinel は CLI (`cli.py`) と MCP サーバー (`mcp_server.py`) が同居している。AI エージェントは MCP 経由で操作するが、editor 制御・マテリアル検査・構造検証・オーバーライド削除は CLI にしかなく、シェル実行が必要だった。

方針: **AI がやる作業は全て MCP で完結させる。CLI は凍結し、Phase 2 で削除する。**

## Scope

- **18 MCP ツール追加**（editor 15 + inspect_materials + validate_structure + revert_overrides）
- ping は除外（MCP ではアセットハイライトの意味がない）
- Git tag `v0.3.0-cli-final` で現状保存（移植作業開始前）
- 破壊的変更なし（追加のみ）
- CLI コード削除は Phase 2（別 spec）
- 全ツールは同期関数（`send_action` / orchestrator / `patch_revert` はいずれも同期 API）

## Architecture

```
MCP ツール（mcp_server.py に追加）
  ├── editor_* (15)    → editor_bridge.send_action() を直接呼ぶ
  ├── inspect_materials → orch.inspect_materials()
  ├── validate_structure → orch.inspect_structure()
  └── revert_overrides  → patch_revert.revert_overrides()
```

- editor 系は orchestrator を経由しない（`editor_bridge.send_action()` を直接呼ぶ）
- inspect_materials / validate_structure は既存の orchestrator メソッドをラップ
- revert_overrides は `patch_revert` モジュールを直接呼ぶ
- 全ツールで `session.resolve_scope()` は不要（path ベースの単一ファイル操作）

## 1. Editor Tools (15)

### Common Pattern

`editor_bridge.send_action()` は環境未設定・タイムアウト時も例外ではなくエラーレスポンス（`{success, severity, code, message, data, diagnostics}`）を返すため、MCP 側で特別なエラーハンドリングは不要。レスポンスをそのまま返す。

```python
@server.tool()
def editor_screenshot(
    view: str = "scene",
    width: int = 0,
    height: int = 0,
) -> dict[str, Any]:
    """Capture a screenshot of the Unity Editor.

    Args:
        view: Which view to capture ("scene" or "game").
        width: Capture width in pixels (0 = current window size).
        height: Capture height in pixels (0 = current window size).
    """
    return send_action(action="capture_screenshot", view=view, width=width, height=height)
```

### Tool Definitions

パラメータ名はワイヤーフォーマット（`send_action` kwargs）に一致させる。MCP 側で名前マッピングしない。

| MCP ツール名 | action | パラメータ | 種別 |
|---|---|---|---|
| `editor_screenshot` | `capture_screenshot` | `view: str = "scene"`, `width: int = 0`, `height: int = 0` | 読取 |
| `editor_select` | `select_object` | `hierarchy_path: str`, `prefab_asset_path: str = ""` | 読取 |
| `editor_frame` | `frame_selected` | `zoom: float = 0.0` | 読取 |
| `editor_camera` | `camera` | `yaw: float = 0.0`, `pitch: float = 0.0`, `distance: float = 0.0` | 読取 |
| `editor_refresh` | `refresh_asset_database` | — | 副作用 |
| `editor_recompile` | `recompile_scripts` | — | 副作用 |
| `editor_instantiate` | `instantiate_to_scene` | `prefab_path: str`, `parent_path: str = ""`, `position: str = ""` | 書込 |
| `editor_set_material` | `set_material` | `renderer_path: str`, `material_index: int`, `material_guid: str` | 書込 |
| `editor_delete` | `delete_object` | `hierarchy_path: str` | 書込 |
| `editor_list_children` | `list_children` | `hierarchy_path: str`, `list_depth: int = 1` | 読取 |
| `editor_list_materials` | `list_materials` | `hierarchy_path: str` | 読取 |
| `editor_list_roots` | `list_roots` | — | 読取 |
| `editor_get_material_property` | `get_material_property` | `renderer_path: str`, `material_index: int`, `property_name: str = ""` | 読取 |
| `editor_console` | `capture_console_logs` | `max_entries: int = 200`, `log_type_filter: str = "all"`, `since_seconds: float = 0.0` | 読取 |
| `editor_run_tests` | `run_integration_tests` | `timeout_sec: int = 300` | 副作用 |

**設計判断:**
- `editor_frame`: CLI には `--zoom` と `--distance`（エイリアス）の 2 パラメータがあるが、ワイヤーフォーマットは `zoom` 1 つ。MCP では `zoom` のみ。
- `editor_camera`: MCP では yaw / pitch / distance 全て `0.0` = 変更なし（bridge 側の挙動）。CLI は `--pitch default=15.0` だが、これは初回使用時の便利値であり MCP では AI が明示的に指定する前提で 0.0 に統一。
- `editor_console`: CLI の `--classify` は CLI 側のポスト処理（`RuntimeValidationService.classify_errors()`）であり、bridge 機能ではない。MCP では省略し、分類が必要なら AI が別途 `RuntimeValidationService` を呼ぶ。
- `editor_instantiate`: `position` は CLI が `"x,y,z"` 文字列を `[float, float, float]` に変換して渡す。MCP でも同じ変換をツール内で行う。
- `editor_run_tests`: CLI は `timeout_sec=300` を固定で渡す。MCP ではデフォルト 300 のパラメータとして公開。

### Kwargs Filtering

`send_action(**kwargs)` に空文字列や 0 を渡しても bridge 側は適切に処理するため、None フィルタリングは不要。ただし `editor_select` の `prefab_asset_path` は空文字列のとき省略する:

```python
@server.tool()
def editor_select(
    hierarchy_path: str,
    prefab_asset_path: str = "",
) -> dict[str, Any]:
    """Select a GameObject in the Unity Hierarchy."""
    kwargs: dict[str, Any] = {"hierarchy_path": hierarchy_path}
    if prefab_asset_path:
        kwargs["prefab_asset_path"] = prefab_asset_path
    return send_action(action="select_object", **kwargs)
```

### Position Parsing (editor_instantiate)

CLI と同じ `"x,y,z"` → `[float, float, float]` 変換を MCP ツール内で行う:

```python
@server.tool()
def editor_instantiate(
    prefab_path: str,
    parent_path: str = "",
    position: str = "",
) -> dict[str, Any]:
    """Instantiate a Prefab into the current Scene."""
    kwargs: dict[str, Any] = {"prefab_path": prefab_path, "parent_path": parent_path}
    if position:
        try:
            parts = [float(v) for v in position.split(",")]
        except ValueError:
            return {"success": False, "severity": "error", "code": "INVALID_POSITION",
                    "message": f"Non-numeric position values: {position} (expected x,y,z)",
                    "data": {}, "diagnostics": []}
        if len(parts) != 3:
            return {"success": False, "severity": "error", "code": "INVALID_POSITION",
                    "message": f"position requires exactly 3 values (x,y,z), got {len(parts)}",
                    "data": {}, "diagnostics": []}
        kwargs["position"] = parts
    return send_action(action="instantiate_to_scene", **kwargs)
```

## 2. Inspection Tools

### inspect_materials

```python
@server.tool()
def inspect_materials(path: str) -> dict[str, Any]:
    """Show per-renderer material slot assignments with override/inherited markers."""
    orch = session.get_orchestrator()
    resp = orch.inspect_materials(target_path=path)
    return resp.to_dict()
```

API 規約: orchestrator 系ツール（`inspect_wiring`, `inspect_variant` と同パターン）。`ToolResponse.to_dict()` でエンベロープ化。

### validate_structure

```python
@server.tool()
def validate_structure(path: str) -> dict[str, Any]:
    """Validate internal YAML structure (fileID duplicates, Transform consistency)."""
    orch = session.get_orchestrator()
    resp = orch.inspect_structure(target_path=path)
    return resp.to_dict()
```

API 規約: orchestrator 系ツール。`ToolResponse.to_dict()` でエンベロープ化。

## 3. revert_overrides

Prefab Variant から特定のプロパティオーバーライドを YAML レベルで削除する。

```python
@server.tool()
def revert_overrides(
    variant_path: str,
    target_file_id: str,
    property_path: str,
    confirm: bool = False,
    change_reason: str = "",
) -> dict[str, Any]:
    """Remove a specific property override from a Prefab Variant.

    Args:
        variant_path: Path to the Prefab Variant file.
        target_file_id: fileID of the target component in the parent prefab.
        property_path: propertyPath of the override to remove (single path).
        confirm: If False (default), dry-run only. If True, apply changes.
        change_reason: Required when confirm=True. Audit log reason.
    """
    from prefab_sentinel.patch_revert import revert_overrides as _revert
    resp = _revert(
        variant_path=variant_path,
        target_file_id=target_file_id,
        property_path=property_path,
        dry_run=not confirm,
        confirm=confirm,
        change_reason=change_reason or None,
    )
    return resp.to_dict()
```

API 規約: `patch_revert.revert_overrides()` は `ToolResponse` を返すため `.to_dict()` が必要。`confirm=False` 時は `dry_run=True` で preview、`confirm=True` 時は実書き込み。

**CLI との差異:**
- CLI は `property_path` が単一文字列（spec 旧版の `property_paths: list[str]` は誤り）
- `target_file_id` は CLI では `--target` 引数で渡される（必須）
- `change_reason` は `confirm=True` 時に必須。bridge 監査ログの要件。

## Test Plan

### Editor tools (15)

`send_action` を mock して:
- 引数が正しいワイヤーフォーマット名で受け渡されること
- レスポンスがそのまま透過すること
- optional パラメータが空文字列のとき kwargs から除外されること（`editor_select` の `prefab_asset_path`）

```python
class TestEditorTools(unittest.TestCase):
    def test_editor_screenshot_delegates(self) -> None:
        server = create_server()
        mock_response = {"success": True, "data": {"output_path": "/tmp/shot.png"}}
        with patch("prefab_sentinel.editor_bridge.send_action", return_value=mock_response):
            _, result = _run(server.call_tool("editor_screenshot", {"view": "game", "width": 1920}))
        self.assertEqual(result, mock_response)

    def test_editor_select_omits_empty_prefab_asset_path(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.editor_bridge.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_select", {"hierarchy_path": "/Root/Child"}))
        _, kwargs = mock_send.call_args
        self.assertNotIn("prefab_asset_path", kwargs)

    def test_editor_set_material_uses_wire_names(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.editor_bridge.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_material", {
                "renderer_path": "/Body",
                "material_index": 0,
                "material_guid": "abc123",
            }))
        mock_send.assert_called_once_with(
            action="set_material",
            renderer_path="/Body",
            material_index=0,
            material_guid="abc123",
        )

    def test_editor_run_tests_sends_timeout(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.editor_bridge.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_run_tests", {}))
        mock_send.assert_called_once_with(action="run_integration_tests", timeout_sec=300)
```

### inspect_materials / validate_structure

orchestrator を mock（既存 `inspect_wiring` テストと同パターン）:

```python
def test_inspect_materials_delegates(self) -> None:
    mock_resp = MagicMock()
    mock_resp.to_dict.return_value = {"success": True, "data": {"renderers": []}}
    mock_orch = MagicMock()
    mock_orch.inspect_materials.return_value = mock_resp
    # ... standard orchestrator mock pattern ...
```

### revert_overrides

`patch_revert.revert_overrides` を mock（`ToolResponse` を返す）:

```python
def test_revert_overrides_dry_run(self) -> None:
    server = create_server()
    mock_resp = MagicMock()
    mock_resp.to_dict.return_value = {
        "success": True, "code": "REVERT_DRY_RUN",
        "data": {"match_count": 2, "read_only": True},
    }
    with patch("prefab_sentinel.patch_revert.revert_overrides", return_value=mock_resp) as mock_revert:
        _, result = _run(server.call_tool("revert_overrides", {
            "variant_path": "Assets/Test.prefab",
            "target_file_id": "12345",
            "property_path": "m_Color.r",
        }))
    mock_revert.assert_called_once_with(
        variant_path="Assets/Test.prefab",
        target_file_id="12345",
        property_path="m_Color.r",
        dry_run=True,
        confirm=False,
        change_reason=None,
    )
    self.assertTrue(result["success"])
```

### test_all_tools_registered 更新

```python
def test_all_tools_registered(self) -> None:
    server = create_server()
    tools = _run(server.list_tools())
    tool_names = {t.name for t in tools}
    expected = {
        # Existing 15 tools
        "activate_project", "get_project_status",
        "get_unity_symbols", "find_unity_symbol", "find_referencing_assets",
        "validate_refs", "inspect_wiring", "inspect_variant",
        "diff_unity_symbols", "set_property",
        "add_component", "remove_component",
        "list_serialized_fields", "validate_field_rename", "check_field_coverage",
        # New 18 tools
        "editor_screenshot", "editor_select", "editor_frame", "editor_camera",
        "editor_refresh", "editor_recompile", "editor_instantiate",
        "editor_set_material", "editor_delete",
        "editor_list_children", "editor_list_materials", "editor_list_roots",
        "editor_get_material_property", "editor_console", "editor_run_tests",
        "inspect_materials", "validate_structure", "revert_overrides",
    }
    self.assertEqual(expected, tool_names)

def test_tool_count(self) -> None:
    server = create_server()
    tools = _run(server.list_tools())
    self.assertEqual(33, len(tools))
```

## File Change Summary

| ファイル | 変更内容 |
|---------|---------|
| `mcp_server.py` | 18 ツール追加 + `editor_bridge` import |
| `tests/test_mcp_server.py` | 18 ツールのテスト追加 + `test_all_tools_registered` / `test_tool_count` 更新 |
| `README.md` | MCP ツール一覧に 18 ツール追加 |

## Review Checklist

修正済みレビュー指摘:

- [x] C1-C10: パラメータ名をワイヤーフォーマットに統一 (`hierarchy_path`, `prefab_asset_path`, `renderer_path`, `material_index`, `property_name`, `log_type_filter`, `since_seconds`, `list_depth`, `prefab_path`, `parent_path`)
- [x] C10: `revert_overrides` 署名を実装に合わせて修正 (`variant_path`, `target_file_id`, `property_path`(単数), `confirm`, `change_reason`)
- [x] I1: ping 除外を Scope に明記
- [x] I2: `editor_camera` defaults は `0.0`（bridge 側で 0 = 変更なし）
- [x] I3: `editor_screenshot` defaults は `width=0, height=0`（0 = 現在ウィンドウサイズ）
- [x] I4: `editor_console` `max_entries` default を 200 に修正
- [x] I5: `revert_overrides` は `ToolResponse` を返す → `.to_dict()` 追加
- [x] I6-I8: テストコードのパラメータ名・mock パターン修正
- [x] S1: `classify` は CLI 側ポスト処理 → MCP では省略
- [x] S2: `editor_run_tests` に `timeout_sec: int = 300` 追加
- [x] S3: `test_all_tools_registered` 期待値を 33 ツールに更新
- [x] S4: 全ツール同期（async 不要）を Scope に明記

## Version

ツール追加のみ（破壊的変更なし）。patch バンプ（pre-commit hook で自動）。
