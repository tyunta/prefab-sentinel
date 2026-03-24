# MCP Consolidation Design — Phase 1: Feature Migration

CLI 専用機能を MCP ツールとして移植し、AI エージェントが全機能を MCP 経由で利用可能にする。

## Background

prefab-sentinel は CLI (`cli.py`) と MCP サーバー (`mcp_server.py`) が同居している。AI エージェントは MCP 経由で操作するが、editor 制御・マテリアル検査・構造検証・オーバーライド削除は CLI にしかなく、シェル実行が必要だった。

方針: **AI がやる作業は全て MCP で完結させる。CLI は凍結し、Phase 2 で削除する。**

## Scope

- **18 MCP ツール追加**（editor 15 + inspect_materials + validate_structure + revert_overrides）
- Git tag `v0.3.0-cli-final` で現状保存（移植作業開始前）
- 破壊的変更なし（追加のみ）
- CLI コード削除は Phase 2（別 spec）

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
    width: int = 800,
    height: int = 600,
) -> dict[str, Any]:
    """Capture a screenshot of the Unity Editor."""
    return send_action(action="capture_screenshot", view=view, width=width, height=height)
```

### Tool Definitions

| MCP ツール名 | action | パラメータ | 種別 |
|---|---|---|---|
| `editor_screenshot` | `capture_screenshot` | `view: str = "scene"`, `width: int = 800`, `height: int = 600` | 読取 |
| `editor_select` | `select_object` | `path: str`, `prefab_stage: str \| None = None` | 読取 |
| `editor_frame` | `frame_selected` | `zoom: float \| None = None`, `distance: float \| None = None` | 読取 |
| `editor_camera` | `camera` | `yaw: float \| None = None`, `pitch: float \| None = None`, `distance: float \| None = None` | 読取 |
| `editor_refresh` | `refresh_asset_database` | — | 副作用 |
| `editor_recompile` | `recompile_scripts` | — | 副作用 |
| `editor_instantiate` | `instantiate_to_scene` | `prefab: str`, `parent: str \| None = None`, `position: str \| None = None` | 書込 |
| `editor_set_material` | `set_material` | `renderer: str`, `index: int`, `material_guid: str` | 書込 |
| `editor_delete` | `delete_object` | `path: str` | 書込 |
| `editor_list_children` | `list_children` | `path: str`, `depth: int = 1` | 読取 |
| `editor_list_materials` | `list_materials` | `path: str` | 読取 |
| `editor_list_roots` | `list_roots` | — | 読取 |
| `editor_get_material_property` | `get_material_property` | `renderer: str`, `index: int`, `property: str \| None = None` | 読取 |
| `editor_console` | `capture_console_logs` | `max_entries: int = 100`, `filter: str = "all"`, `since: float \| None = None`, `classify: bool = False` | 読取 |
| `editor_run_tests` | `run_integration_tests` | — | 副作用 |

### Kwargs Filtering

`send_action(**kwargs)` に `None` 値を渡さないよう、各ツールで optional パラメータが `None` の場合は kwargs から除外する:

```python
@server.tool()
def editor_select(
    path: str,
    prefab_stage: str | None = None,
) -> dict[str, Any]:
    """Select a GameObject in the Unity Hierarchy."""
    kwargs: dict[str, Any] = {"path": path}
    if prefab_stage is not None:
        kwargs["prefab_stage"] = prefab_stage
    return send_action(action="select_object", **kwargs)
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

API 規約: orchestrator 系ツール（`inspect_wiring`, `inspect_variant` と同パターン）。

### validate_structure

```python
@server.tool()
def validate_structure(path: str) -> dict[str, Any]:
    """Validate internal YAML structure (fileID duplicates, Transform consistency)."""
    orch = session.get_orchestrator()
    resp = orch.inspect_structure(target_path=path)
    return resp.to_dict()
```

API 規約: orchestrator 系ツール。

## 3. revert_overrides

Prefab Variant から特定のプロパティオーバーライドを YAML レベルで削除する。

```python
@server.tool()
def revert_overrides(
    path: str,
    property_paths: list[str],
    confirm: bool = False,
) -> dict[str, Any]:
    """Remove specific property overrides from a Prefab Variant.

    Args:
        path: Path to the Prefab Variant file.
        property_paths: List of propertyPath strings to revert.
        confirm: If False (default), dry-run only. If True, apply changes.
    """
    from prefab_sentinel.patch_revert import revert_overrides as _revert
    return _revert(path=path, property_paths=property_paths, dry_run=not confirm)
```

API 規約: 操作系ツール（`set_property` と同パターン、dry-run / confirm ゲート付き）。

## Test Plan

### Editor tools (15)

`send_action` を mock して:
- 引数が正しく受け渡されること
- レスポンスがそのまま透過すること
- optional パラメータが None のとき kwargs から除外されること

```python
class TestEditorTools(unittest.TestCase):
    def test_editor_screenshot_delegates(self) -> None:
        server = create_server()
        mock_response = {"success": True, "data": {"output_path": "/tmp/shot.png"}}
        with patch("prefab_sentinel.editor_bridge.send_action", return_value=mock_response):
            result = _run(server.call_tool("editor_screenshot", {"view": "game", "width": 1920}))
        self.assertEqual(result, mock_response)

    def test_editor_select_omits_none_kwargs(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.editor_bridge.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_select", {"path": "/Root/Child"}))
        _, kwargs = mock_send.call_args
        self.assertNotIn("prefab_stage", kwargs)
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

`patch_revert.revert_overrides` を mock:

```python
def test_revert_overrides_dry_run(self) -> None:
    server = create_server()
    with patch("prefab_sentinel.patch_revert.revert_overrides", return_value={"reverted": 2, "dry_run": True}) as mock_revert:
        result = _run(server.call_tool("revert_overrides", {
            "path": "Assets/Test.prefab",
            "property_paths": ["m_Color.r", "m_Color.g"],
        }))
    mock_revert.assert_called_once_with(path="Assets/Test.prefab", property_paths=["m_Color.r", "m_Color.g"], dry_run=True)
```

## File Change Summary

| ファイル | 変更内容 |
|---------|---------|
| `mcp_server.py` | 18 ツール追加 + `editor_bridge` import |
| `tests/test_mcp_server.py` | 18 ツールのテスト追加 |
| `README.md` | MCP ツール一覧に 18 ツール追加 |

## Version

ツール追加のみ（破壊的変更なし）。patch バンプ（pre-commit hook で自動）。
