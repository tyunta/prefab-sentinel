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


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Regex patterns for .mat parsing
# ---------------------------------------------------------------------------

_SHADER_REF = re.compile(
    r"m_Shader:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?(?:,\s*type:\s*(-?\d+))?\}"
)
_NAME = re.compile(r"m_Name:\s*(.+)")
_KEYWORDS = re.compile(r"m_ShaderKeywords:\s*(.*)")
_LIGHTMAP_FLAGS = re.compile(r"m_LightmapFlags:\s*(\d+)")
_INSTANCING = re.compile(r"m_EnableInstancingVariants:\s*(\d+)")
_DOUBLE_SIDED_GI = re.compile(r"m_DoubleSidedGI:\s*(\d+)")
_RENDER_QUEUE = re.compile(r"m_CustomRenderQueue:\s*(-?\d+)")

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


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def inspect_material_asset(
    target_path: str,
    project_root: Path | None = None,
) -> MaterialAssetResult:
    """Parse a .mat file and return structured material data.

    Args:
        target_path: Path to a ``.mat`` file.
        project_root: Unity project root for GUID resolution. Auto-detected
            if *None*.

    Returns:
        ``MaterialAssetResult`` with shader, properties, and texture
        references.

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
        shader_fid = m.group(1)
        shader_guid = normalize_guid(m.group(2)) if m.group(2) else ""
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
                guid_index = collect_project_guid_index(
                    proj_root, include_package_cache=False,
                )
            asset = guid_index.get(guid)
            if asset is not None:
                try:
                    tex_path = asset.resolve().relative_to(
                        proj_root.resolve(),
                    ).as_posix()
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
    floats = [
        MaterialFloat(name=fm.group(1), value=float(fm.group(2)))
        for fm in _FLOAT_ENTRY.finditer(float_section)
    ]

    # --- m_Colors ---
    color_section = _extract_section(text, "m_Colors")
    colors = [
        MaterialColor(
            name=cm.group(1),
            value={
                "r": float(cm.group(2)),
                "g": float(cm.group(3)),
                "b": float(cm.group(4)),
                "a": float(cm.group(5)),
            },
        )
        for cm in _COLOR_ENTRY.finditer(color_section)
    ]

    # --- m_Ints ---
    int_section = _extract_section(text, "m_Ints")
    ints = [
        MaterialInt(name=im.group(1), value=int(im.group(2)))
        for im in _INT_ENTRY.finditer(int_section)
    ]

    return MaterialAssetResult(
        target_path=target_path,
        material_name=material_name,
        shader=ShaderInfo(
            guid=shader_guid,
            file_id=shader_fid,
            name=shader_name,
            path=shader_path,
        ),
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


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


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
