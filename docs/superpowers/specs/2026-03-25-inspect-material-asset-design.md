# inspect_material_asset Design Spec

## Purpose

Add a read-only MCP tool that parses a `.mat` file and returns structured data about its shader, properties, and texture references. Primary consumer is AI agents that need to understand material configuration for inspection, wiring validation, or edit planning.

## Input

- `asset_path: str` — Path to a `.mat` file (absolute or project-relative).

## Output Schema

```json
{
  "target_path": "Assets/Foo/Bar.mat",
  "read_only": true,
  "material_name": "Switch_Base",
  "shader": {
    "guid": "0000000000000000f000000000000000",
    "file_id": "46",
    "name": "Standard",
    "path": null
  },
  "keywords": ["_EMISSION", "_METALLICGLOSSMAP"],
  "render_queue": -1,
  "lightmap_flags": 2,
  "gpu_instancing": false,
  "double_sided_gi": false,
  "properties": {
    "textures": [
      {
        "name": "_MainTex",
        "guid": "08921c71040fe584f932a539f42adebb",
        "path": "Assets/Textures/base.png",
        "scale": [1.0, 1.0],
        "offset": [0.0, 0.0]
      }
    ],
    "floats": [
      {"name": "_Glossiness", "value": 1.0}
    ],
    "colors": [
      {"name": "_Color", "value": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}}
    ],
    "ints": [
      {"name": "_StencilRef", "value": 0}
    ]
  },
  "texture_count": 1,
  "float_count": 1,
  "color_count": 1,
  "int_count": 1,
  "tree": "Switch_Base (Standard)\n  _MainTex: base.png (Assets/Textures/base.png)\n  _Glossiness: 1.0\n  _Color: (1.0, 1.0, 1.0, 1.0)"
}
```

### Field details

- **shader.name**: Resolved from a built-in shader map (GUID `0000000000000000f000000000000000` + fileID → known name) or from GUID index for custom shaders. Falls back to `"Unknown (fileID=N)"`. Initial built-in map: Standard (46), Standard (Specular setup) (45), Unlit/Color (10700), Unlit/Texture (10750), Unlit/Transparent (10760), Unlit/Transparent Cutout (10770), Sprites/Default (10753), UI/Default (10782). The map is best-effort; unlisted fileIDs use the fallback. This map is separate from `builtin_assets.py` (which handles asset-level references, not shader fileIDs within the `f000...` GUID namespace).
- **shader.path**: `null` for built-in shaders; project-relative path for custom shaders.
- **render_queue**: Raw value from `m_CustomRenderQueue`. `-1` means "use shader default."
- **properties.textures**: Only slots with an assigned texture (`fileID != 0`). Unset slots are omitted to reduce noise.
- **properties.ints**: Present only when the `.mat` file contains `m_Ints`. Empty array if absent.
- **properties.texture_count / float_count / color_count / int_count**: Top-level summary counts for quick complexity assessment.
- **tree**: Human-readable text summary. Format: `"MaterialName (ShaderName)\n  _PropName: assetName (path)\n  _FloatProp: value\n  _Color: (r, g, b, a)"`. Not the primary output — structured data is.

## Design Decisions

1. **Unset textures omitted** — `fileID: 0` slots are noise for AI consumers. Only assigned slots are returned.
2. **Built-in shader name resolution** — A static map covers common Unity built-in shaders by fileID (see Field details). Unknown fileIDs fall back to `"Unknown (fileID=N)"`.
3. **GUID resolution is best-effort** — If a project root is configured and the GUID index is available, texture/shader GUIDs are resolved to asset paths. If not, the GUID string is returned without error.
4. **m_Ints support** — Newer Unity versions include `m_Ints` in `m_SavedProperties`. Parsed when present, empty array when absent.
5. **Naming: `material_asset_inspector` vs `material_inspector`** — `material_inspector.py` inspects material *slots on renderers* in `.prefab`/`.unity` files. `material_asset_inspector.py` inspects the `.mat` *asset file itself*. The `_asset` suffix distinguishes the two. Docstrings in both modules and the orchestrator methods must clarify this distinction.
6. **`m_StringTagMap` and `disabledShaderPasses` excluded** — These fields are out of scope for the initial implementation. They can be added later if AI agents need render pipeline tag information.

## Error Codes

Follows the orchestrator-layer convention (`INSPECT_*` descriptive strings).

| Code | Severity | Meaning |
|------|----------|---------|
| INSPECT_MATERIAL_ASSET_RESULT | info | Success |
| INSPECT_MATERIAL_ASSET_NOT_MAT | error | File is not a `.mat` file |
| INSPECT_MATERIAL_ASSET_READ_ERROR | error | File read or parse failure |

File-not-found is handled by the orchestrator's `_read_target_file` helper, which returns a `*_FILE_NOT_FOUND` error before the parser is invoked.

## File Changes

| File | Change |
|------|--------|
| `prefab_sentinel/material_asset_inspector.py` | **New** — parser, dataclasses, formatter |
| `prefab_sentinel/orchestrator.py` | Add `inspect_material_asset()` method |
| `prefab_sentinel/mcp_server.py` | Register MCP tool |
| `tests/test_material_asset_inspector.py` | **New** — unit tests |
| `tests/fixtures/` | Add `.mat` test fixtures |
| `README.md` | Add tool description |

## Parsing Strategy

Unity `.mat` files are single-document YAML with class ID 21. The parser:

1. Reads the file and strips the Unity YAML header (`%YAML`, `%TAG`, `--- !u!21`).
2. Extracts top-level Material fields: `m_Name`, `m_Shader`, `m_ShaderKeywords`, `m_LightmapFlags`, `m_EnableInstancingVariants`, `m_DoubleSidedGI`, `m_CustomRenderQueue`.
3. Parses `m_SavedProperties` subsections:
   - `m_TexEnvs`: list of `- _PropertyName:` blocks with `m_Texture` (reference), `m_Scale`, `m_Offset`.
   - `m_Floats`: list of `- _PropertyName: value` entries.
   - `m_Colors`: list of `- _PropertyName: {r, g, b, a}` entries.
   - `m_Ints`: list of `- _PropertyName: value` entries (if present).
4. Resolves shader and texture GUIDs via the existing GUID index infrastructure (`unity_assets.py`).
5. Filters out unset texture slots (`fileID: 0`).

Parsing uses regex on the raw text (consistent with `material_inspector.py` patterns), not a full YAML parser, since Unity YAML is not standard-compliant.

The orchestrator method follows the standard inspection pattern:
1. `self._read_target_file(target_path, "INSPECT_MATERIAL_ASSET")` — handles file-not-found.
2. Check suffix is `.mat`; return `INSPECT_MATERIAL_ASSET_NOT_MAT` if not.
3. Delegate to `inspect_material_asset()` in `material_asset_inspector.py`.
4. Transform result into data dict with summary counts.
5. Return via `success_response` / `error_response`.

## Scope Boundaries

### In scope
- Read-only inspection of `.mat` file content.
- Shader and texture GUID resolution.
- Structured + text output.

### Out of scope
- Editing `.mat` properties (use `set_property` or `editor_set_material`).
- Comparing two materials (future feature).
- Shader property metadata from `.shader` files (would require shader parsing).
- `m_StringTagMap` and `disabledShaderPasses` fields (can be added later).

## Test Fixtures

| Fixture | Purpose |
|---------|---------|
| `standard_textured.mat` | Standard shader with textures, floats, colors |
| `no_textures.mat` | All texture slots `fileID: 0` (tests omission) |
| `with_ints.mat` | Material with `m_Ints` section |
| `custom_shader.mat` | Non-built-in shader GUID |
| `malformed.mat` | Truncated/invalid content (error path) |
