# inspect_material_asset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only MCP tool that parses `.mat` files and returns structured shader/property data for AI agent consumption.

**Architecture:** New `material_asset_inspector.py` module with regex-based parser + dataclasses. Orchestrator wraps the parser with `_read_target_file` + suffix validation. MCP layer delegates to orchestrator. TDD: fixtures and tests first, then implementation.

**Tech Stack:** Python 3.12, regex parsing, existing `unity_assets.py` GUID infrastructure, `unittest`

**Spec:** `docs/superpowers/specs/2026-03-25-inspect-material-asset-design.md`

---

### Task 1: Create test fixtures

**Files:**
- Create: `tests/fixtures/mat/standard_textured.mat`
- Create: `tests/fixtures/mat/no_textures.mat`
- Create: `tests/fixtures/mat/with_ints.mat`
- Create: `tests/fixtures/mat/custom_shader.mat`
- Create: `tests/fixtures/mat/malformed.mat`

- [ ] **Step 1: Create `standard_textured.mat`** — Standard shader with 2 assigned textures, floats, and colors

```yaml
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  serializedVersion: 6
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_Name: TestMaterial
  m_Shader: {fileID: 46, guid: 0000000000000000f000000000000000, type: 0}
  m_ShaderKeywords: _EMISSION _METALLICGLOSSMAP
  m_LightmapFlags: 2
  m_EnableInstancingVariants: 0
  m_DoubleSidedGI: 0
  m_CustomRenderQueue: -1
  stringTagMap: {}
  disabledShaderPasses: []
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _MainTex:
        m_Texture: {fileID: 2800000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1, type: 3}
        m_Scale: {x: 1, y: 1}
        m_Offset: {x: 0, y: 0}
    - _BumpMap:
        m_Texture: {fileID: 0}
        m_Scale: {x: 1, y: 1}
        m_Offset: {x: 0, y: 0}
    - _EmissionMap:
        m_Texture: {fileID: 2800000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2, type: 3}
        m_Scale: {x: 2, y: 2}
        m_Offset: {x: 0.5, y: 0.5}
    m_Floats:
    - _BumpScale: 1
    - _Cutoff: 0.5
    - _Glossiness: 0.8
    - _Metallic: 0.5
    - _Mode: 0
    m_Colors:
    - _Color: {r: 1, g: 0.5, b: 0.25, a: 1}
    - _EmissionColor: {r: 0, g: 0, b: 0, a: 1}
```

- [ ] **Step 2: Create `no_textures.mat`** — All texture slots unset

```yaml
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  serializedVersion: 6
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_Name: NoTextures
  m_Shader: {fileID: 46, guid: 0000000000000000f000000000000000, type: 0}
  m_ShaderKeywords:
  m_LightmapFlags: 4
  m_EnableInstancingVariants: 0
  m_DoubleSidedGI: 0
  m_CustomRenderQueue: -1
  stringTagMap: {}
  disabledShaderPasses: []
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _MainTex:
        m_Texture: {fileID: 0}
        m_Scale: {x: 1, y: 1}
        m_Offset: {x: 0, y: 0}
    - _BumpMap:
        m_Texture: {fileID: 0}
        m_Scale: {x: 1, y: 1}
        m_Offset: {x: 0, y: 0}
    m_Floats:
    - _Glossiness: 0.5
    - _Metallic: 0
    m_Colors:
    - _Color: {r: 1, g: 1, b: 1, a: 1}
```

- [ ] **Step 3: Create `with_ints.mat`** — Material with `m_Ints` section

```yaml
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  serializedVersion: 6
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_Name: WithInts
  m_Shader: {fileID: 46, guid: 0000000000000000f000000000000000, type: 0}
  m_ShaderKeywords:
  m_LightmapFlags: 4
  m_EnableInstancingVariants: 1
  m_DoubleSidedGI: 1
  m_CustomRenderQueue: 2000
  stringTagMap: {}
  disabledShaderPasses: []
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _MainTex:
        m_Texture: {fileID: 0}
        m_Scale: {x: 1, y: 1}
        m_Offset: {x: 0, y: 0}
    m_Ints:
    - _StencilRef: 128
    - _ZTest: 4
    m_Floats:
    - _Glossiness: 0.5
    m_Colors:
    - _Color: {r: 1, g: 1, b: 1, a: 1}
```

- [ ] **Step 4: Create `custom_shader.mat`** — Non-built-in shader

```yaml
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  serializedVersion: 6
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_Name: CustomShaderMat
  m_Shader: {fileID: 4800000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 3}
  m_ShaderKeywords: _ALPHATEST_ON
  m_LightmapFlags: 4
  m_EnableInstancingVariants: 0
  m_DoubleSidedGI: 0
  m_CustomRenderQueue: 2450
  stringTagMap: {}
  disabledShaderPasses: []
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _MainTex:
        m_Texture: {fileID: 2800000, guid: cccccccccccccccccccccccccccccccc, type: 3}
        m_Scale: {x: 1, y: 1}
        m_Offset: {x: 0, y: 0}
    m_Floats:
    - _Cutoff: 0.5
    m_Colors:
    - _Color: {r: 1, g: 1, b: 1, a: 1}
```

- [ ] **Step 5: Create `malformed.mat`** — Truncated content

```
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
This is not a valid material file
```

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/mat/
git commit -m "test: add .mat fixtures for inspect_material_asset"
```

---

### Task 2: Dataclasses and built-in shader map

**Files:**
- Create: `prefab_sentinel/material_asset_inspector.py`

- [ ] **Step 1: Write test for dataclass construction and built-in shader lookup**

Create `tests/test_material_asset_inspector.py`:

```python
"""Tests for prefab_sentinel.material_asset_inspector."""

from __future__ import annotations

import unittest
from pathlib import Path

from prefab_sentinel.material_asset_inspector import (
    MaterialAssetResult,
    MaterialTexture,
    MaterialFloat,
    MaterialColor,
    MaterialInt,
    ShaderInfo,
    resolve_builtin_shader_name,
)


class TestBuiltinShaderMap(unittest.TestCase):
    def test_standard_shader(self) -> None:
        self.assertEqual(resolve_builtin_shader_name("46"), "Standard")

    def test_standard_specular(self) -> None:
        self.assertEqual(resolve_builtin_shader_name("45"), "Standard (Specular setup)")

    def test_unlit_color(self) -> None:
        self.assertEqual(resolve_builtin_shader_name("10700"), "Unlit/Color")

    def test_unknown_file_id(self) -> None:
        self.assertEqual(resolve_builtin_shader_name("99999"), "Unknown (fileID=99999)")


class TestDataclasses(unittest.TestCase):
    def test_material_asset_result_construction(self) -> None:
        result = MaterialAssetResult(
            target_path="Assets/Test.mat",
            material_name="Test",
            shader=ShaderInfo(guid="abc", file_id="46", name="Standard", path=None),
            keywords=[],
            render_queue=-1,
            lightmap_flags=4,
            gpu_instancing=False,
            double_sided_gi=False,
            textures=[],
            floats=[],
            colors=[],
            ints=[],
        )
        self.assertEqual(result.material_name, "Test")
        self.assertIsNone(result.shader.path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_asset_inspector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prefab_sentinel.material_asset_inspector'`

- [ ] **Step 3: Write dataclasses and shader map**

Create `prefab_sentinel/material_asset_inspector.py`:

```python
"""Material asset (.mat) inspector for Unity material files.

Parses .mat files to extract shader info, texture references, and property
values. Unlike ``material_inspector.py`` (which inspects material *slots on
renderers* in .prefab/.unity files), this module inspects the .mat asset
file *itself*.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.unity_assets import (
    collect_project_guid_index,
    decode_text_file,
    find_project_root,
    is_unity_builtin_guid,
    normalize_guid,
    resolve_scope_path,
)

logger = logging.getLogger(__name__)

# Built-in shader fileID map for GUID 0000000000000000f000000000000000.
# Separate from builtin_assets.py which handles asset-level references.
_BUILTIN_SHADER_NAMES: dict[str, str] = {
    "45": "Standard (Specular setup)",
    "46": "Standard",
    "10700": "Unlit/Color",
    "10750": "Unlit/Texture",
    "10760": "Unlit/Transparent",
    "10770": "Unlit/Transparent Cutout",
    "10753": "Sprites/Default",
    "10782": "UI/Default",
}

def resolve_builtin_shader_name(file_id: str) -> str:
    """Resolve a built-in shader fileID to a human-readable name."""
    return _BUILTIN_SHADER_NAMES.get(file_id, f"Unknown (fileID={file_id})")


@dataclass(slots=True)
class ShaderInfo:
    """Shader reference on a material."""

    guid: str
    file_id: str
    name: str
    path: str | None  # None for built-in shaders


@dataclass(slots=True)
class MaterialTexture:
    """An assigned texture slot."""

    name: str
    guid: str
    path: str  # Resolved asset path, or empty if unresolvable
    scale: list[float]
    offset: list[float]


@dataclass(slots=True)
class MaterialFloat:
    """A float property."""

    name: str
    value: float


@dataclass(slots=True)
class MaterialColor:
    """A color property."""

    name: str
    value: dict[str, float]  # {r, g, b, a}


@dataclass(slots=True)
class MaterialInt:
    """An integer property."""

    name: str
    value: int


@dataclass(slots=True)
class MaterialAssetResult:
    """Complete result of inspecting a .mat file."""

    target_path: str
    material_name: str
    shader: ShaderInfo
    keywords: list[str]
    render_queue: int
    lightmap_flags: int
    gpu_instancing: bool
    double_sided_gi: bool
    textures: list[MaterialTexture]
    floats: list[MaterialFloat]
    colors: list[MaterialColor]
    ints: list[MaterialInt]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_asset_inspector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/material_asset_inspector.py tests/test_material_asset_inspector.py
git commit -m "feat: add material_asset_inspector dataclasses and shader map"
```

---

### Task 3: Parser — `inspect_material_asset()` function

**Files:**
- Modify: `prefab_sentinel/material_asset_inspector.py`
- Modify: `tests/test_material_asset_inspector.py`

- [ ] **Step 1: Write tests for the parser using fixtures**

Append to `tests/test_material_asset_inspector.py`:

```python
FIXTURES = Path(__file__).parent / "fixtures" / "mat"


class TestInspectMaterialAsset(unittest.TestCase):
    def test_standard_textured(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "standard_textured.mat"))
        self.assertEqual(result.material_name, "TestMaterial")
        self.assertEqual(result.shader.name, "Standard")
        self.assertEqual(result.shader.file_id, "46")
        self.assertIsNone(result.shader.path)
        self.assertEqual(result.keywords, ["_EMISSION", "_METALLICGLOSSMAP"])
        self.assertEqual(result.render_queue, -1)
        self.assertEqual(result.lightmap_flags, 2)
        self.assertFalse(result.gpu_instancing)
        self.assertFalse(result.double_sided_gi)
        # Only assigned textures (fileID != 0)
        self.assertEqual(len(result.textures), 2)
        self.assertEqual(result.textures[0].name, "_MainTex")
        self.assertEqual(result.textures[0].guid, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1")
        self.assertEqual(result.textures[1].name, "_EmissionMap")
        self.assertAlmostEqual(result.textures[1].scale[0], 2.0)
        self.assertAlmostEqual(result.textures[1].offset[0], 0.5)
        # Floats
        self.assertEqual(len(result.floats), 5)
        glossiness = next(f for f in result.floats if f.name == "_Glossiness")
        self.assertAlmostEqual(glossiness.value, 0.8)
        # Colors
        self.assertEqual(len(result.colors), 2)
        color = next(c for c in result.colors if c.name == "_Color")
        self.assertAlmostEqual(color.value["g"], 0.5)

    def test_no_textures(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "no_textures.mat"))
        self.assertEqual(result.textures, [])
        self.assertEqual(result.material_name, "NoTextures")

    def test_with_ints(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "with_ints.mat"))
        self.assertEqual(len(result.ints), 2)
        stencil = next(i for i in result.ints if i.name == "_StencilRef")
        self.assertEqual(stencil.value, 128)
        self.assertTrue(result.gpu_instancing)
        self.assertTrue(result.double_sided_gi)
        self.assertEqual(result.render_queue, 2000)

    def test_custom_shader(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "custom_shader.mat"))
        self.assertEqual(result.shader.guid, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        self.assertEqual(result.shader.file_id, "4800000")
        # Without GUID index, name falls back
        self.assertIn("Unknown", result.shader.name)
        self.assertEqual(result.keywords, ["_ALPHATEST_ON"])
        self.assertEqual(result.render_queue, 2450)

    def test_malformed_raises(self) -> None:
        with self.assertRaises(ValueError):
            inspect_material_asset(str(FIXTURES / "malformed.mat"))
```

Add `inspect_material_asset` to the import block at the top.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_asset_inspector.py::TestInspectMaterialAsset -v`
Expected: FAIL — `ImportError: cannot import name 'inspect_material_asset'`

- [ ] **Step 3: Implement the parser**

Add to `prefab_sentinel/material_asset_inspector.py`:

```python
# --- Regex patterns for .mat parsing ---

_SHADER_REF = re.compile(
    r"m_Shader:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?(?:,\s*type:\s*(-?\d+))?\}"
)
_NAME = re.compile(r"m_Name:\s*(.+)")
_KEYWORDS = re.compile(r"m_ShaderKeywords:\s*(.*)")
_LIGHTMAP_FLAGS = re.compile(r"m_LightmapFlags:\s*(\d+)")
_INSTANCING = re.compile(r"m_EnableInstancingVariants:\s*(\d+)")
_DOUBLE_SIDED_GI = re.compile(r"m_DoubleSidedGI:\s*(\d+)")
_RENDER_QUEUE = re.compile(r"m_CustomRenderQueue:\s*(-?\d+)")

# m_TexEnvs entry: "    - _PropName:\n        m_Texture: {ref}\n        m_Scale: {x: N, y: N}\n        m_Offset: {x: N, y: N}"
_TEX_ENTRY = re.compile(
    r"- (\w+):\s*\n"
    r"\s+m_Texture:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?(?:,\s*type:\s*(-?\d+))?\}\s*\n"
    r"\s+m_Scale:\s*\{x:\s*([\d.e+-]+),\s*y:\s*([\d.e+-]+)\}\s*\n"
    r"\s+m_Offset:\s*\{x:\s*([\d.e+-]+),\s*y:\s*([\d.e+-]+)\}"
)

_FLOAT_ENTRY = re.compile(r"- (\w+):\s*([\d.e+-]+)")
_COLOR_ENTRY = re.compile(
    r"- (\w+):\s*\{r:\s*([\d.e+-]+),\s*g:\s*([\d.e+-]+),\s*b:\s*([\d.e+-]+),\s*a:\s*([\d.e+-]+)\}"
)
_INT_ENTRY = re.compile(r"- (\w+):\s*(-?\d+)")

def _extract_section(text: str, section_name: str) -> str:
    """Extract a subsection of m_SavedProperties by name."""
    pattern = re.compile(
        rf"^\s{{4}}{re.escape(section_name)}:\s*\n(.*?)(?=^\s{{4}}m_\w+:|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    return m.group(1) if m else ""


def inspect_material_asset(
    target_path: str,
    project_root: Path | None = None,
) -> MaterialAssetResult:
    """Parse a .mat file and return structured material data.

    Args:
        target_path: Path to a ``.mat`` file.
        project_root: Unity project root for GUID resolution. Auto-detected if None.

    Returns:
        ``MaterialAssetResult`` with shader, properties, and texture references.

    Raises:
        ValueError: If the file does not contain a valid Material block.
        OSError: If the file cannot be read.
    """
    proj_root = project_root or find_project_root(Path(target_path))
    path = resolve_scope_path(target_path, proj_root)
    text = decode_text_file(path)

    # Validate it's a Material file
    if "--- !u!21 " not in text:
        raise ValueError(f"Not a valid Material file: {target_path}")

    # Material name
    m = _NAME.search(text)
    material_name = m.group(1).strip() if m else ""

    # Shader reference
    m = _SHADER_REF.search(text)
    if m:
        shader_fid, shader_guid, _ = m.group(1), m.group(2) or "", m.group(3) or ""
        shader_guid = normalize_guid(shader_guid) if shader_guid else ""
    else:
        shader_fid, shader_guid = "0", ""

    # Shared GUID index — built lazily, reused across shader + texture resolution
    guid_index: dict[str, Path] | None = None

    # Resolve shader name
    if shader_guid and is_unity_builtin_guid(shader_guid):
        shader_name = resolve_builtin_shader_name(shader_fid)
        shader_path = None
    elif shader_guid:
        guid_index = collect_project_guid_index(proj_root, include_package_cache=False)
        asset = guid_index.get(shader_guid)
        if asset is not None:
            try:
                shader_path = asset.resolve().relative_to(proj_root.resolve()).as_posix()
            except ValueError:
                shader_path = asset.as_posix()
            shader_name = asset.stem
        else:
            shader_name = f"Unknown (fileID={shader_fid})"
            shader_path = None
    else:
        shader_name = f"Unknown (fileID={shader_fid})"
        shader_path = None

    # Keywords
    m = _KEYWORDS.search(text)
    kw_str = m.group(1).strip() if m else ""
    keywords = kw_str.split() if kw_str else []

    # Scalar fields
    m = _RENDER_QUEUE.search(text)
    render_queue = int(m.group(1)) if m else -1

    m = _LIGHTMAP_FLAGS.search(text)
    lightmap_flags = int(m.group(1)) if m else 4

    m = _INSTANCING.search(text)
    gpu_instancing = m.group(1) != "0" if m else False

    m = _DOUBLE_SIDED_GI.search(text)
    double_sided_gi = m.group(1) != "0" if m else False

    # --- m_TexEnvs ---
    tex_section = _extract_section(text, "m_TexEnvs")
    textures: list[MaterialTexture] = []
    for tm in _TEX_ENTRY.finditer(tex_section):
        name, fid, guid = tm.group(1), tm.group(2), tm.group(3) or ""
        if fid == "0":
            continue  # Unset slot
        guid = normalize_guid(guid) if guid else ""
        # Resolve texture path
        tex_path = ""
        if guid:
            if guid_index is None:
                guid_index = collect_project_guid_index(proj_root, include_package_cache=False)
            asset = guid_index.get(guid)
            if asset is not None:
                try:
                    tex_path = asset.resolve().relative_to(proj_root.resolve()).as_posix()
                except ValueError:
                    tex_path = asset.as_posix()
        textures.append(MaterialTexture(
            name=name,
            guid=guid,
            path=tex_path,
            scale=[float(tm.group(5)), float(tm.group(6))],
            offset=[float(tm.group(7)), float(tm.group(8))],
        ))

    # --- m_Floats ---
    float_section = _extract_section(text, "m_Floats")
    floats = [MaterialFloat(name=fm.group(1), value=float(fm.group(2)))
              for fm in _FLOAT_ENTRY.finditer(float_section)]

    # --- m_Colors ---
    color_section = _extract_section(text, "m_Colors")
    colors = [MaterialColor(
        name=cm.group(1),
        value={"r": float(cm.group(2)), "g": float(cm.group(3)),
               "b": float(cm.group(4)), "a": float(cm.group(5))},
    ) for cm in _COLOR_ENTRY.finditer(color_section)]

    # --- m_Ints ---
    int_section = _extract_section(text, "m_Ints")
    ints = [MaterialInt(name=im.group(1), value=int(im.group(2)))
            for im in _INT_ENTRY.finditer(int_section)]

    return MaterialAssetResult(
        target_path=target_path,
        material_name=material_name,
        shader=ShaderInfo(guid=shader_guid, file_id=shader_fid, name=shader_name, path=shader_path),
        keywords=keywords,
        render_queue=render_queue,
        lightmap_flags=lightmap_flags,
        gpu_instancing=gpu_instancing,
        double_sided_gi=double_sided_gi,
        textures=textures,
        floats=floats,
        colors=colors,
        ints=ints,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_asset_inspector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/material_asset_inspector.py tests/test_material_asset_inspector.py
git commit -m "feat: implement inspect_material_asset parser"
```

---

### Task 4: Formatter — `format_material_asset()`

**Files:**
- Modify: `prefab_sentinel/material_asset_inspector.py`
- Modify: `tests/test_material_asset_inspector.py`

- [ ] **Step 1: Write test for formatter**

Append to `tests/test_material_asset_inspector.py`:

```python
from prefab_sentinel.material_asset_inspector import format_material_asset


class TestFormatMaterialAsset(unittest.TestCase):
    def test_basic_format(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "standard_textured.mat"))
        tree = format_material_asset(result)
        self.assertIn("TestMaterial (Standard)", tree)
        self.assertIn("_MainTex:", tree)
        self.assertIn("_Glossiness: 0.8", tree)
        self.assertIn("_Color: (1.0, 0.5, 0.25, 1.0)", tree)

    def test_no_textures_format(self) -> None:
        result = inspect_material_asset(str(FIXTURES / "no_textures.mat"))
        tree = format_material_asset(result)
        self.assertIn("NoTextures (Standard)", tree)
        # No texture lines
        self.assertNotIn("_MainTex:", tree)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_asset_inspector.py::TestFormatMaterialAsset -v`
Expected: FAIL — `ImportError: cannot import name 'format_material_asset'`

- [ ] **Step 3: Implement formatter**

Add to `prefab_sentinel/material_asset_inspector.py`:

```python
def format_material_asset(result: MaterialAssetResult) -> str:
    """Format material asset inspection result as human-readable text."""
    lines: list[str] = [f"{result.material_name} ({result.shader.name})"]
    for tex in result.textures:
        name_part = Path(tex.path).name if tex.path else tex.guid
        path_part = f" ({tex.path})" if tex.path else ""
        lines.append(f"  {tex.name}: {name_part}{path_part}")
    for f in result.floats:
        lines.append(f"  {f.name}: {f.value}")
    for c in result.colors:
        v = c.value
        lines.append(f"  {c.name}: ({v['r']}, {v['g']}, {v['b']}, {v['a']})")
    for i in result.ints:
        lines.append(f"  {i.name}: {i.value}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_asset_inspector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/material_asset_inspector.py tests/test_material_asset_inspector.py
git commit -m "feat: add format_material_asset formatter"
```

---

### Task 5: Orchestrator integration

**Files:**
- Modify: `prefab_sentinel/orchestrator.py`
- Modify: `tests/test_material_asset_inspector.py`

- [ ] **Step 1: Write test for orchestrator method**

Append to `tests/test_material_asset_inspector.py`:

```python
import os


class TestOrchestratorInspectMaterialAsset(unittest.TestCase):
    def test_success(self) -> None:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        orch = Phase1Orchestrator.default(project_root=FIXTURES.parent.parent)
        resp = orch.inspect_material_asset(target_path=str(FIXTURES / "standard_textured.mat"))
        self.assertTrue(resp.success)
        self.assertEqual(resp.code, "INSPECT_MATERIAL_ASSET_RESULT")
        self.assertEqual(resp.data["material_name"], "TestMaterial")
        self.assertEqual(resp.data["shader"]["name"], "Standard")
        self.assertEqual(resp.data["texture_count"], 2)
        self.assertIn("tree", resp.data)

    def test_not_mat_file(self) -> None:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        orch = Phase1Orchestrator.default(project_root=FIXTURES.parent.parent)
        resp = orch.inspect_material_asset(target_path=str(FIXTURES.parent / "smoke" / "basic.prefab"))
        self.assertFalse(resp.success)
        self.assertEqual(resp.code, "INSPECT_MATERIAL_ASSET_NOT_MAT")

    def test_file_not_found(self) -> None:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        orch = Phase1Orchestrator.default(project_root=FIXTURES.parent.parent)
        resp = orch.inspect_material_asset(target_path="nonexistent.mat")
        self.assertFalse(resp.success)
        self.assertIn("FILE_NOT_FOUND", resp.code)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_asset_inspector.py::TestOrchestratorInspectMaterialAsset -v`
Expected: FAIL — `AttributeError: 'Orchestrator' object has no attribute 'inspect_material_asset'`

- [ ] **Step 3: Implement orchestrator method**

Add import at the top of `orchestrator.py` (near the `inspect_materials` import):

```python
from prefab_sentinel.material_asset_inspector import (
    format_material_asset,
    inspect_material_asset as _inspect_material_asset,
)
```

Add method to `Orchestrator` class (after the `inspect_materials` method):

```python
    def inspect_material_asset(
        self,
        target_path: str,
    ) -> ToolResponse:
        """Inspect shader and properties of a .mat asset file (read-only).

        Unlike ``inspect_materials`` (which inspects material *slots on
        renderers* in .prefab/.unity files), this inspects the .mat asset
        file *itself*.

        Args:
            target_path: Path to a ``.mat`` file.

        Returns:
            ``ToolResponse`` with shader info, property data, and summary
            counts.
        """
        text_or_error = self._read_target_file(target_path, "INSPECT_MATERIAL_ASSET")
        if isinstance(text_or_error, ToolResponse):
            return text_or_error

        suffix = Path(target_path).suffix.lower()
        if suffix != ".mat":
            return error_response(
                "INSPECT_MATERIAL_ASSET_NOT_MAT",
                f"Expected a .mat file, got {suffix}",
                data={"target_path": target_path, "read_only": True},
            )

        try:
            result = _inspect_material_asset(target_path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return error_response(
                "INSPECT_MATERIAL_ASSET_READ_ERROR",
                f"Failed to inspect material asset: {exc}",
                data={"target_path": target_path, "read_only": True},
            )

        tree_text = format_material_asset(result)

        tex_data = [
            {
                "name": t.name,
                "guid": t.guid,
                "path": t.path,
                "scale": t.scale,
                "offset": t.offset,
            }
            for t in result.textures
        ]
        float_data = [{"name": f.name, "value": f.value} for f in result.floats]
        color_data = [{"name": c.name, "value": c.value} for c in result.colors]
        int_data = [{"name": i.name, "value": i.value} for i in result.ints]

        data: dict[str, object] = {
            "target_path": target_path,
            "read_only": True,
            "material_name": result.material_name,
            "shader": {
                "guid": result.shader.guid,
                "file_id": result.shader.file_id,
                "name": result.shader.name,
                "path": result.shader.path,
            },
            "keywords": result.keywords,
            "render_queue": result.render_queue,
            "lightmap_flags": result.lightmap_flags,
            "gpu_instancing": result.gpu_instancing,
            "double_sided_gi": result.double_sided_gi,
            "properties": {
                "textures": tex_data,
                "floats": float_data,
                "colors": color_data,
                "ints": int_data,
            },
            "texture_count": len(result.textures),
            "float_count": len(result.floats),
            "color_count": len(result.colors),
            "int_count": len(result.ints),
            "tree": tree_text,
        }

        return success_response(
            "INSPECT_MATERIAL_ASSET_RESULT",
            "inspect.material_asset completed (read-only).",
            data=data,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_asset_inspector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prefab_sentinel/orchestrator.py tests/test_material_asset_inspector.py
git commit -m "feat: add inspect_material_asset to orchestrator"
```

---

### Task 6: MCP tool registration

**Files:**
- Modify: `prefab_sentinel/mcp_server.py`

- [ ] **Step 1: Register MCP tool**

Add after the `inspect_materials` tool registration in `mcp_server.py`:

```python
    @server.tool()
    def inspect_material_asset(asset_path: str) -> dict[str, Any]:
        """Inspect shader, properties, and texture references in a .mat file (read-only).

        Returns structured data about the material's shader, texture slots,
        float/color/int properties, and summary counts. Unset texture slots
        are omitted.

        Args:
            asset_path: Path to a .mat file.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_material_asset(target_path=asset_path)
        return resp.to_dict()
```

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/ -v --timeout=60`
Expected: PASS (all existing + new tests)

- [ ] **Step 3: Commit**

```bash
git add prefab_sentinel/mcp_server.py
git commit -m "feat: register inspect_material_asset MCP tool"
```

---

### Task 7: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add tool to README**

Find the `inspect_materials` row in the tool table and add after it:

```markdown
| `inspect_material_asset` | .mat ファイルのシェーダー・プロパティ・テクスチャ参照を構造化データで返す（read-only） |
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add inspect_material_asset to README"
```

---

### Task 8: Full test suite verification

- [ ] **Step 1: Run full test suite**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/ -v --timeout=60`
Expected: All PASS, no regressions

- [ ] **Step 2: Run the new tests in isolation to confirm count**

Run: `cd /mnt/d/git/prefab-sentinel && uv run pytest tests/test_material_asset_inspector.py -v`
Expected: All tests pass (builtin shader map, dataclasses, parser x5, formatter x2, orchestrator x3)
