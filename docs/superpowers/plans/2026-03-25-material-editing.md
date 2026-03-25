# Material Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Material property editing — runtime via Editor Bridge and offline via YAML rewriting.

**Architecture:** Two independent paths sharing a common value format convention. 2.1 adds a new C# handler + bridge action + MCP tool for runtime editing. 2.2 adds `write_material_property()` in `material_asset_inspector.py` for YAML rewriting, exposed through orchestrator + MCP.

**Tech Stack:** Python 3.11+, Unity C# (EditorBridge), MCP (FastMCP), unittest

**Spec:** `docs/superpowers/specs/2026-03-25-material-editing-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:22-40,44-85,134-155,196-251,922-1011` | Add `HandleSetMaterialProperty` + DTO fields |
| Modify | `prefab_sentinel/editor_bridge.py:33-52` | Add `"set_material_property"` to SUPPORTED_ACTIONS |
| Modify | `prefab_sentinel/mcp_server.py:945-962` | Add `editor_set_material_property` tool after `editor_get_material_property` |
| Modify | `prefab_sentinel/material_asset_inspector.py` | Add `write_material_property()` function |
| Modify | `prefab_sentinel/orchestrator.py` | Add `set_material_property()` method |
| Modify | `prefab_sentinel/mcp_server.py` | Add `set_material_property` MCP tool |
| Modify | `tests/test_mcp_server.py:43-67` | Update tool registration test |
| Modify | `tests/test_editor_bridge.py:164-186` | Update SUPPORTED_ACTIONS test |
| Create | `tests/test_material_write.py` | Tests for `write_material_property()` |

---

## Task 1: C# `HandleSetMaterialProperty`

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs`

- [ ] **Step 1: Add `"set_material_property"` to SupportedActions (line 22-40)**

After `"get_material_property",` (line 39), add:
```csharp
            "set_material_property",
```

- [ ] **Step 2: Add `property_value` field to EditorControlRequest (after line 84)**

After `public string property_name = string.Empty;` add:
```csharp
            // set_material_property
            public string property_value = string.Empty;  // raw JSON string, manually parsed by handler
```

- [ ] **Step 3: Add dispatch case (after `get_material_property` case, around line 258)**

After the `get_material_property` case, before `run_integration_tests`:
```csharp
                case "set_material_property":
                    response = HandleSetMaterialProperty(request);
                    break;
```

- [ ] **Step 4: Implement `HandleSetMaterialProperty` (after `HandleGetMaterialProperty`, around line 1011)**

Insert before `CollectChildren`:

```csharp
        private static EditorControlResponse HandleSetMaterialProperty(EditorControlRequest request)
        {
            if (string.IsNullOrEmpty(request.hierarchy_path))
                return BuildError("EDITOR_CTRL_MISSING_PATH", "hierarchy_path is required for set_material_property.");
            if (request.material_index < 0)
                return BuildError("EDITOR_CTRL_MISSING_INDEX", "material_index is required (>= 0).");
            if (string.IsNullOrEmpty(request.property_name))
                return BuildError("EDITOR_CTRL_MISSING_PROPERTY", "property_name is required.");
            if (string.IsNullOrEmpty(request.property_value))
                return BuildError("EDITOR_CTRL_MISSING_VALUE", "property_value is required.");

            var go = GameObject.Find(request.hierarchy_path);
            if (go == null)
                return BuildError("EDITOR_CTRL_OBJECT_NOT_FOUND",
                    $"GameObject not found: {request.hierarchy_path}");

            var renderer = go.GetComponent<Renderer>();
            if (renderer == null)
                return BuildError("EDITOR_CTRL_NO_RENDERER",
                    $"No Renderer on: {request.hierarchy_path}");

            var mats = renderer.sharedMaterials;
            if (request.material_index >= mats.Length)
                return BuildError("EDITOR_CTRL_INDEX_OOB",
                    $"material_index {request.material_index} out of range (length={mats.Length}).");

            var mat = mats[request.material_index];
            if (mat == null)
                return BuildError("EDITOR_CTRL_MATERIAL_NULL",
                    $"Material at index {request.material_index} is null.");

            var shader = mat.shader;
            if (shader == null)
                return BuildError("EDITOR_CTRL_SHADER_NULL",
                    $"Material at index {request.material_index} has no shader assigned.");

            // Find property in shader
            int propIdx = shader.FindPropertyIndex(request.property_name);
            if (propIdx < 0)
                return BuildError("EDITOR_CTRL_PROPERTY_NOT_FOUND",
                    $"Property '{request.property_name}' not found on shader '{shader.name}'.");

            var propType = shader.GetPropertyType(propIdx);
            string val = request.property_value;

            Undo.RecordObject(mat, $"Set {request.property_name}");

            try
            {
                switch (propType)
                {
                    case UnityEngine.Rendering.ShaderPropertyType.Float:
                    case UnityEngine.Rendering.ShaderPropertyType.Range:
                        mat.SetFloat(request.property_name, float.Parse(val, System.Globalization.CultureInfo.InvariantCulture));
                        break;

                    case UnityEngine.Rendering.ShaderPropertyType.Int:
                        mat.SetInteger(request.property_name, int.Parse(val, System.Globalization.CultureInfo.InvariantCulture));
                        break;

                    case UnityEngine.Rendering.ShaderPropertyType.Color:
                    {
                        // Parse "[r, g, b, a]"
                        var trimmed = val.Trim('[', ']');
                        var parts = trimmed.Split(',');
                        if (parts.Length != 4)
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                $"Color requires [r,g,b,a], got {parts.Length} components.");
                        mat.SetColor(request.property_name, new Color(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture)));
                        break;
                    }

                    case UnityEngine.Rendering.ShaderPropertyType.Vector:
                    {
                        var trimmed = val.Trim('[', ']');
                        var parts = trimmed.Split(',');
                        if (parts.Length != 4)
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                $"Vector requires [x,y,z,w], got {parts.Length} components.");
                        mat.SetVector(request.property_name, new Vector4(
                            float.Parse(parts[0].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[1].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[2].Trim(), System.Globalization.CultureInfo.InvariantCulture),
                            float.Parse(parts[3].Trim(), System.Globalization.CultureInfo.InvariantCulture)));
                        break;
                    }

                    case UnityEngine.Rendering.ShaderPropertyType.Texture:
                    {
                        if (string.IsNullOrEmpty(val))
                        {
                            mat.SetTexture(request.property_name, null);
                        }
                        else if (val.StartsWith("guid:"))
                        {
                            string guid = val.Substring(5);
                            string texPath = AssetDatabase.GUIDToAssetPath(guid);
                            if (string.IsNullOrEmpty(texPath))
                                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                    $"Texture GUID not found: {guid}");
                            var tex = AssetDatabase.LoadAssetAtPath<Texture>(texPath);
                            if (tex == null)
                                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                    $"Failed to load texture at: {texPath}");
                            mat.SetTexture(request.property_name, tex);
                        }
                        else
                        {
                            return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                                "Texture value must be 'guid:<hex>' or empty string for null.");
                        }
                        break;
                    }

                    default:
                        return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                            $"Unsupported property type: {propType}");
                }
            }
            catch (FormatException ex)
            {
                return BuildError("EDITOR_CTRL_PROPERTY_TYPE_MISMATCH",
                    $"Failed to parse value '{val}' for {propType}: {ex.Message}");
            }

            // Read back the value to confirm
            string readBack;
            switch (propType)
            {
                case UnityEngine.Rendering.ShaderPropertyType.Color:
                    readBack = mat.GetColor(request.property_name).ToString();
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Float:
                case UnityEngine.Rendering.ShaderPropertyType.Range:
                    readBack = mat.GetFloat(request.property_name).ToString("G9");
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Vector:
                    readBack = mat.GetVector(request.property_name).ToString();
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Texture:
                    var readTex = mat.GetTexture(request.property_name);
                    readBack = readTex != null ? AssetDatabase.GetAssetPath(readTex) : "(none)";
                    break;
                case UnityEngine.Rendering.ShaderPropertyType.Int:
                    readBack = mat.GetInteger(request.property_name).ToString();
                    break;
                default:
                    readBack = "(unknown)";
                    break;
            }

            return BuildSuccess("EDITOR_CTRL_SET_MATERIAL_PROPERTY_OK",
                $"Set {request.property_name} on material '{mat.name}'",
                data: new EditorControlData
                {
                    material_properties = new[] { new MaterialPropertyEntry
                    {
                        property_name = request.property_name,
                        property_type = propType.ToString(),
                        value = readBack
                    }},
                    total_entries = 1,
                    executed = true
                });
        }
```

- [ ] **Step 5: Commit**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
git commit -m "feat(bridge): add HandleSetMaterialProperty C# handler"
```

---

## Task 2: Python Bridge + MCP Tool for `editor_set_material_property`

**Files:**
- Modify: `prefab_sentinel/editor_bridge.py:33-52`
- Modify: `prefab_sentinel/mcp_server.py:945-962`
- Modify: `tests/test_mcp_server.py:43-67`
- Modify: `tests/test_editor_bridge.py:164-186`

- [ ] **Step 1: Add `"set_material_property"` to SUPPORTED_ACTIONS in `editor_bridge.py`**

After `"get_material_property",` (line 49), add:
```python
        "set_material_property",
```

- [ ] **Step 2: Add `editor_set_material_property` MCP tool in `mcp_server.py`**

After the `editor_get_material_property` tool (after line 962), add:

```python
    @server.tool()
    def editor_set_material_property(
        hierarchy_path: str,
        material_index: int,
        property_name: str,
        value: str,
    ) -> dict[str, Any]:
        """Set a shader property value on a material at runtime.

        Type is determined from shader definition (not from the value format).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            property_name: Shader property name (e.g. "_Color", "_MainTex").
            value: Value as string. Format depends on shader type:
                Float/Range: "0.5"
                Int: "2"
                Color: "[1, 0.8, 0.6, 1]" (RGBA)
                Vector: "[0, 1, 0, 0]" (XYZW)
                Texture: "guid:abc123..." or "" (null)
        """
        return send_action(
            action="set_material_property",
            hierarchy_path=hierarchy_path,
            material_index=material_index,
            property_name=property_name,
            property_value=value,
        )
```

- [ ] **Step 3: Update tool registration test in `tests/test_mcp_server.py`**

Add `"editor_set_material_property"` to the expected set (line 52 area, after `"editor_get_material_property"`).

Update `test_tool_count`: change `38` to `39`.

- [ ] **Step 4: Update SUPPORTED_ACTIONS test in `tests/test_editor_bridge.py`**

Add `"set_material_property"` to the expected set in `TestSupportedActions.test_all_actions_present`.

- [ ] **Step 5: Add `send_action` delegation test**

Add to the existing delegation test class in `tests/test_mcp_server.py`:

```python
def test_editor_set_material_property_delegates(self) -> None:
    with patch("prefab_sentinel.mcp_server.send_action", return_value={"success": True}) as mock_send:
        result = _run(invoke_tool("editor_set_material_property", {
            "hierarchy_path": "/Foo",
            "material_index": 0,
            "property_name": "_Color",
            "value": "[1, 0, 0, 1]",
        }))
        mock_send.assert_called_once_with(
            action="set_material_property",
            hierarchy_path="/Foo",
            material_index=0,
            property_name="_Color",
            property_value="[1, 0, 0, 1]",
        )
```

- [ ] **Step 6: Run tests**

```bash
cd /mnt/d/git/prefab-sentinel && uv run --extra test python -m unittest tests.test_mcp_server.TestToolRegistration tests.test_editor_bridge.TestSupportedActions tests.test_editor_bridge.TestCameraActions -v
```

Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/editor_bridge.py prefab_sentinel/mcp_server.py tests/test_mcp_server.py tests/test_editor_bridge.py
git commit -m "feat(mcp): add editor_set_material_property tool"
```

---

## Task 3: `write_material_property()` in `material_asset_inspector.py`

**Files:**
- Modify: `prefab_sentinel/material_asset_inspector.py`
- Create: `tests/test_material_write.py`

- [ ] **Step 1: Write tests for `write_material_property()`**

Create `tests/test_material_write.py`:

```python
"""Tests for material_asset_inspector.write_material_property()."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.material_asset_inspector import (
    inspect_material_asset,
    write_material_property,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "mat"


class TestWriteMaterialPropertyFloat(unittest.TestCase):
    """Test float property writing."""

    def test_dry_run_returns_before_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_Glossiness", "0.3", dry_run=True)
            self.assertTrue(result["success"])
            self.assertEqual("MAT_PROP_DRY_RUN", result["code"])
            self.assertEqual("m_Floats", result["data"]["category"])
            self.assertAlmostEqual(0.8, float(result["data"]["before"]))
            self.assertEqual("0.3", result["data"]["after"])

    def test_confirm_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_Glossiness", "0.3", dry_run=False)
            self.assertTrue(result["success"])
            self.assertEqual("MAT_PROP_APPLIED", result["code"])
            # Re-parse to verify
            parsed = inspect_material_asset(str(mat))
            glossiness = next(f for f in parsed.floats if f.name == "_Glossiness")
            self.assertAlmostEqual(0.3, glossiness.value)


class TestWriteMaterialPropertyInt(unittest.TestCase):
    """Test integer property writing."""

    def test_confirm_writes_int(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "with_ints.mat", mat)
            result = write_material_property(str(mat), "_StencilRef", "64", dry_run=False)
            self.assertTrue(result["success"])
            parsed = inspect_material_asset(str(mat))
            stencil = next(i for i in parsed.ints if i.name == "_StencilRef")
            self.assertEqual(64, stencil.value)


class TestWriteMaterialPropertyColor(unittest.TestCase):
    """Test color property writing."""

    def test_confirm_writes_color(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(
                str(mat), "_Color", "[0.5, 0.6, 0.7, 1]", dry_run=False,
            )
            self.assertTrue(result["success"])
            parsed = inspect_material_asset(str(mat))
            color = next(c for c in parsed.colors if c.name == "_Color")
            self.assertAlmostEqual(0.5, color.value["r"])
            self.assertAlmostEqual(0.6, color.value["g"])
            self.assertAlmostEqual(0.7, color.value["b"])
            self.assertAlmostEqual(1.0, color.value["a"])


class TestWriteMaterialPropertyTexture(unittest.TestCase):
    """Test texture property writing."""

    def test_confirm_changes_texture_guid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            new_guid = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            result = write_material_property(
                str(mat), "_MainTex", f"guid:{new_guid}", dry_run=False,
            )
            self.assertTrue(result["success"])
            # Read raw text to verify GUID changed
            text = mat.read_text(encoding="utf-8")
            self.assertIn(new_guid, text)
            # Verify m_Scale/m_Offset preserved
            self.assertIn("m_Scale: {x: 1, y: 1}", text)

    def test_confirm_nullifies_texture(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_MainTex", "", dry_run=False)
            self.assertTrue(result["success"])
            text = mat.read_text(encoding="utf-8")
            self.assertIn("m_Texture: {fileID: 0}", text)


class TestWriteMaterialPropertyErrors(unittest.TestCase):
    """Test error cases."""

    def test_wrong_extension(self) -> None:
        result = write_material_property("/tmp/test.prefab", "_Foo", "1", dry_run=True)
        self.assertFalse(result["success"])
        self.assertEqual("MAT_PROP_WRONG_EXT", result["code"])

    def test_file_not_found(self) -> None:
        result = write_material_property("/nonexistent/test.mat", "_Foo", "1", dry_run=True)
        self.assertFalse(result["success"])
        self.assertEqual("MAT_PROP_FILE_NOT_FOUND", result["code"])

    def test_property_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_NonExistent", "1", dry_run=True)
            self.assertFalse(result["success"])
            self.assertEqual("MAT_PROP_NOT_FOUND", result["code"])
            # Should list available properties
            self.assertTrue(len(result["diagnostics"]) > 0)

    def test_color_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mat = Path(tmpdir) / "test.mat"
            shutil.copy(_FIXTURES / "standard_textured.mat", mat)
            result = write_material_property(str(mat), "_Color", "not_a_color", dry_run=False)
            self.assertFalse(result["success"])
            self.assertEqual("MAT_PROP_PARSE_ERROR", result["code"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/d/git/prefab-sentinel && uv run --extra test python -m unittest tests.test_material_write -v
```

Expected: FAIL (write_material_property not defined)

- [ ] **Step 3: Implement `write_material_property()` in `material_asset_inspector.py`**

Add at the end of the file (after `format_material_asset`):

```python
# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def _error_dict(code: str, message: str, data: dict | None = None, diagnostics: list | None = None) -> dict:
    return {
        "success": False,
        "severity": "error",
        "code": code,
        "message": message,
        "data": data or {},
        "diagnostics": diagnostics or [],
    }


def _success_dict(code: str, message: str, data: dict | None = None) -> dict:
    return {
        "success": True,
        "severity": "info",
        "code": code,
        "message": message,
        "data": data or {},
        "diagnostics": [],
    }


def write_material_property(
    target_path: str,
    property_name: str,
    value: str,
    *,
    dry_run: bool = True,
) -> dict:
    """Write a single property value in a .mat file.

    Args:
        target_path: Path to the .mat file.
        property_name: Property name (e.g. ``_Glossiness``).
        value: New value as string. Format:
            Float/Int: ``"0.5"``
            Color: ``"[r, g, b, a]"``
            Texture: ``"guid:abc..."`` or ``""`` (null)
        dry_run: If True, return preview without writing.

    Returns:
        Envelope dict with ``success``, ``code``, ``data``.
    """
    import json as _json

    path = Path(target_path)

    if path.suffix.lower() != ".mat":
        return _error_dict("MAT_PROP_WRONG_EXT", f"Expected .mat file, got {path.suffix}")

    if not path.exists():
        return _error_dict("MAT_PROP_FILE_NOT_FOUND", f"File not found: {target_path}")

    text = path.read_text(encoding="utf-8")

    # Determine category and current value
    category, before, section_name = _find_property(text, property_name)
    if category is None:
        all_names = _list_all_property_names(text)
        return _error_dict(
            "MAT_PROP_NOT_FOUND",
            f"Property '{property_name}' not found in {path.name}",
            data={"available_properties": all_names},
            diagnostics=[{"detail": f"Available: {', '.join(all_names)}"}],
        )

    data = {
        "asset_path": target_path,
        "property_name": property_name,
        "category": section_name,
        "before": str(before),
        "after": value,
    }

    if dry_run:
        return _success_dict(
            "MAT_PROP_DRY_RUN",
            f"Would change {property_name} from {before} to {value}",
            data=data,
        )

    # Apply the replacement
    try:
        new_text = _replace_property(text, property_name, value, category)
    except ValueError as exc:
        return _error_dict("MAT_PROP_PARSE_ERROR", str(exc))

    path.write_text(new_text, encoding="utf-8")

    # Verify by re-parsing
    verify_cat, verify_val, _ = _find_property(new_text, property_name)
    if verify_cat is None:
        return _error_dict("MAT_PROP_VERIFY_FAILED", "Property disappeared after write")

    return _success_dict(
        "MAT_PROP_APPLIED",
        f"Changed {property_name} from {before} to {value}",
        data=data,
    )


def _find_property(
    text: str, property_name: str,
) -> tuple[str | None, str | None, str | None]:
    """Find a property across all 4 categories.

    Returns (category, current_value_str, section_name) or (None, None, None).
    """
    # m_Floats
    float_section = _extract_section(text, "m_Floats")
    for m in _FLOAT_ENTRY.finditer(float_section):
        if m.group(1) == property_name:
            return "float", m.group(2), "m_Floats"

    # m_Ints
    int_section = _extract_section(text, "m_Ints")
    for m in _INT_ENTRY.finditer(int_section):
        if m.group(1) == property_name:
            return "int", m.group(2), "m_Ints"

    # m_Colors
    color_section = _extract_section(text, "m_Colors")
    for m in _COLOR_ENTRY.finditer(color_section):
        if m.group(1) == property_name:
            return "color", f"[{m.group(2)}, {m.group(3)}, {m.group(4)}, {m.group(5)}]", "m_Colors"

    # m_TexEnvs
    tex_section = _extract_section(text, "m_TexEnvs")
    for m in _TEX_ENTRY.finditer(tex_section):
        if m.group(1) == property_name:
            guid = m.group(3) or ""
            return "texture", f"guid:{guid}" if guid else "", "m_TexEnvs"

    return None, None, None


def _list_all_property_names(text: str) -> list[str]:
    """Collect all property names from all categories."""
    names: list[str] = []
    for section, pattern in [
        ("m_Floats", _FLOAT_ENTRY),
        ("m_Ints", _INT_ENTRY),
        ("m_Colors", _COLOR_ENTRY),
        ("m_TexEnvs", _TEX_ENTRY),
    ]:
        section_text = _extract_section(text, section)
        for m in pattern.finditer(section_text):
            names.append(m.group(1))
    return sorted(set(names))


def _replace_property(text: str, property_name: str, value: str, category: str) -> str:
    """Replace a property value in the full .mat text."""
    import json as _json

    if category == "float":
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*)([\d.e+-]+)"
        )
        try:
            float(value)
        except ValueError:
            raise ValueError(f"Invalid float value: {value}")
        return pattern.sub(rf"\g<1>{value}", text, count=1)

    elif category == "int":
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*)(-?\d+)"
        )
        try:
            int(value)
        except ValueError:
            raise ValueError(f"Invalid int value: {value}")
        return pattern.sub(rf"\g<1>{value}", text, count=1)

    elif category == "color":
        # Parse [r, g, b, a]
        try:
            parts = _json.loads(value)
            if not isinstance(parts, list) or len(parts) != 4:
                raise ValueError("Color must be [r, g, b, a]")
            r, g, b, a = [float(x) for x in parts]
        except (ValueError, TypeError, _json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid color value '{value}': {exc}")
        new_val = f"{{r: {r}, g: {g}, b: {b}, a: {a}}}"
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*)\{{r:\s*[\d.e+-]+,\s*g:\s*[\d.e+-]+,\s*b:\s*[\d.e+-]+,\s*a:\s*[\d.e+-]+\}}"
        )
        return pattern.sub(rf"\g<1>{new_val}", text, count=1)

    elif category == "texture":
        # Find the entry block for this property name, then replace only m_Texture line
        if value == "":
            new_texture = "m_Texture: {fileID: 0}"
        elif value.startswith("guid:"):
            guid = value[5:]
            new_texture = f"m_Texture: {{fileID: 2800000, guid: {guid}, type: 3}}"
        else:
            raise ValueError(f"Texture value must be 'guid:<hex>' or empty, got: {value}")

        # Pattern: within the named tex entry block, replace the m_Texture line
        entry_pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*\n\s+)(m_Texture:\s*\{{[^}}]+\}})"
        )
        return entry_pattern.sub(rf"\g<1>{new_texture}", text, count=1)

    raise ValueError(f"Unknown category: {category}")
```

- [ ] **Step 4: Run tests**

```bash
cd /mnt/d/git/prefab-sentinel && uv run --extra test python -m unittest tests.test_material_write -v
```

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/material_asset_inspector.py tests/test_material_write.py
git commit -m "feat: add write_material_property() for offline .mat editing"
```

---

## Task 4: Orchestrator + MCP Tool for `set_material_property`

**Files:**
- Modify: `prefab_sentinel/orchestrator.py`
- Modify: `prefab_sentinel/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Add `set_material_property()` to orchestrator**

Add import at top of `prefab_sentinel/orchestrator.py` (within the `material_asset_inspector` import block):

```python
from prefab_sentinel.material_asset_inspector import (
    format_material_asset,
    inspect_material_asset as _inspect_material_asset,
    write_material_property as _write_material_property,
)
```

Add method to `Phase1Orchestrator` (after `inspect_material_asset`, around line 1050):

```python
    def set_material_property(
        self,
        target_path: str,
        property_name: str,
        value: str,
        *,
        dry_run: bool = True,
        change_reason: str | None = None,
    ) -> ToolResponse:
        """Set a single property in a .mat file.

        Args:
            target_path: Path to a .mat file.
            property_name: Property name (e.g. ``_Glossiness``).
            value: New value as string.
            dry_run: If True, preview only.
            change_reason: Required when dry_run=False.

        Returns:
            ``ToolResponse`` with before/after data.
        """
        if not dry_run and not change_reason:
            return error_response(
                "MAT_PROP_REASON_REQUIRED",
                "change_reason is required when confirm=True",
            )

        result = _write_material_property(
            target_path, property_name, value, dry_run=dry_run,
        )

        if not dry_run and result.get("success"):
            result["data"]["auto_refresh"] = self.maybe_auto_refresh()

        severity = Severity.INFO if result["success"] else Severity.ERROR
        return ToolResponse(
            success=result["success"],
            severity=severity,
            code=result["code"],
            message=result["message"],
            data=result.get("data", {}),
            diagnostics=[
                Diagnostic(
                    path=target_path,
                    location="",
                    detail=d.get("detail", ""),
                    evidence=d.get("evidence", ""),
                )
                for d in result.get("diagnostics", [])
            ],
        )
```

- [ ] **Step 2: Add `set_material_property` MCP tool**

In `prefab_sentinel/mcp_server.py`, add after the `inspect_material_asset` tool (around line 1105):

```python
    @server.tool()
    def set_material_property(
        asset_path: str,
        property_name: str,
        value: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Set a single property in a .mat file (offline YAML editing).

        Two-phase workflow:
        - confirm=False (default): dry-run preview showing before/after.
        - confirm=True: applies the change and writes back.

        Value format depends on property category:
        - Float: "0.5"
        - Int: "2"
        - Color: "[1, 0.8, 0.6, 1]" (RGBA)
        - Texture: "guid:abc123..." or "" (null)

        Args:
            asset_path: Path to the .mat file.
            property_name: Property name (e.g. "_Glossiness", "_Color").
            value: New value as string.
            confirm: Set True to apply (False = dry-run only).
            change_reason: Required when confirm=True. Audit log reason.
        """
        orch = session.get_orchestrator()
        resp = orch.set_material_property(
            target_path=asset_path,
            property_name=property_name,
            value=value,
            dry_run=not confirm,
            change_reason=change_reason or None,
        )
        return resp.to_dict()
```

- [ ] **Step 3: Update tool registration test**

Add `"set_material_property"` to the expected set in `tests/test_mcp_server.py`.

Update tool count: `39` → `40`.

- [ ] **Step 4: Run tests**

```bash
cd /mnt/d/git/prefab-sentinel && uv run --extra test python -m unittest tests.test_mcp_server.TestToolRegistration tests.test_material_write -v
```

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/orchestrator.py prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add set_material_property tool for offline .mat editing"
```

---

## Task 5: Full Regression + README + Documentation

**Files:**
- Run: full test suite
- Modify: `README.md`

- [ ] **Step 1: Run full test suite**

```bash
cd /mnt/d/git/prefab-sentinel && uv run --extra test python -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK)"
```

Expected: All tests PASS

- [ ] **Step 2: Run lint**

```bash
cd /mnt/d/git/prefab-sentinel && uv run ruff check prefab_sentinel/ tests/ --select I001,F
```

Expected: No errors (fix if needed)

- [ ] **Step 3: Update README tool table**

Add two new rows to the MCP tools table in `README.md`:

```markdown
| `editor_set_material_property` | ランタイムでシェーダープロパティを設定（型はシェーダー定義から自動判定、Undo 対応） |
| `set_material_property` | .mat ファイルのプロパティをオフライン YAML 編集（dry-run/confirm ゲート付き） |
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add editor_set_material_property and set_material_property to README"
```
