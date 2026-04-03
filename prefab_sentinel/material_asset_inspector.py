"""Material asset (.mat) inspector for Unity material files.

Parses .mat files to extract shader info, texture references, and property
values. Unlike ``material_inspector.py`` (which inspects material *slots on
renderers* in .prefab/.unity files), this module inspects the .mat asset
file *itself*.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.contracts import error_dict as _error_dict, success_dict as _success_dict
from prefab_sentinel.fuzzy_match import suggest_similar
from prefab_sentinel.json_io import load_json
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


def _find_property(
    text: str,
    property_name: str,
) -> tuple[str | None, str | None, str | None]:
    """Find a property across all 4 section categories.

    Returns ``(category, current_value_str, section_name)`` or
    ``(None, None, None)`` when the property is not found.
    """
    float_section = _extract_section(text, "m_Floats")
    for m in _FLOAT_ENTRY.finditer(float_section):
        if m.group(1) == property_name:
            return "float", m.group(2), "m_Floats"

    int_section = _extract_section(text, "m_Ints")
    for m in _INT_ENTRY.finditer(int_section):
        if m.group(1) == property_name:
            return "int", m.group(2), "m_Ints"

    color_section = _extract_section(text, "m_Colors")
    for m in _COLOR_ENTRY.finditer(color_section):
        if m.group(1) == property_name:
            r, g, b, a = m.group(2), m.group(3), m.group(4), m.group(5)
            return "color", f"[{r}, {g}, {b}, {a}]", "m_Colors"

    tex_section = _extract_section(text, "m_TexEnvs")
    for m in _TEX_ENTRY.finditer(tex_section):
        if m.group(1) == property_name:
            guid = m.group(3) or ""
            return "texture", f"guid:{guid}" if guid else "", "m_TexEnvs"

    return None, None, None


def _list_all_property_names(text: str) -> list[str]:
    """Collect all property names from every section category."""
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


def _section_span(text: str, section_name: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` byte offsets of a section in *text*."""
    pattern = re.compile(
        rf"^(\s{{4}}{re.escape(section_name)}:\s*\n)(.*?)(?=^\s{{4}}m_\w+:|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if m is None:
        return None
    # Return span covering the header plus body.
    return m.start(), m.end()


def _replace_in_section(
    text: str,
    section_name: str,
    pattern: re.Pattern[str],
    replacement: str,
) -> str:
    """Apply a regex substitution restricted to *section_name* only.

    This avoids accidental matches in other sections (e.g. an int entry
    that also matches the float pattern).
    """
    span = _section_span(text, section_name)
    if span is None:
        raise ValueError(f"Section {section_name} not found")
    start, end = span
    section_text = text[start:end]
    new_section = pattern.sub(replacement, section_text, count=1)
    return text[:start] + new_section + text[end:]


def _replace_property(
    text: str,
    property_name: str,
    value: str,
    category: str,
) -> str:
    """Replace a property value in the full .mat text."""
    if category == "float":
        try:
            float(value)
        except ValueError:
            raise ValueError(f"Invalid float value: {value}") from None
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*)[\d.e+-]+",
        )
        return _replace_in_section(text, "m_Floats", pattern, rf"\g<1>{value}")

    if category == "int":
        try:
            int(value)
        except ValueError:
            raise ValueError(f"Invalid int value: {value}") from None
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*)-?\d+",
        )
        return _replace_in_section(text, "m_Ints", pattern, rf"\g<1>{value}")

    if category == "color":
        try:
            parts = load_json(value)
            if not isinstance(parts, list) or len(parts) != 4:
                raise ValueError("Color must be [r, g, b, a]")
            r, g, b, a = (float(x) for x in parts)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid color value '{value}': {exc}") from None
        new_val = f"{{r: {r}, g: {g}, b: {b}, a: {a}}}"
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*)"
            r"\{r:\s*[\d.e+-]+,\s*g:\s*[\d.e+-]+,\s*b:\s*[\d.e+-]+,\s*a:\s*[\d.e+-]+\}",
        )
        return _replace_in_section(text, "m_Colors", pattern, rf"\g<1>{new_val}")

    if category == "texture":
        if value == "":
            new_texture = "m_Texture: {fileID: 0}"
        elif value.startswith("guid:"):
            guid = value[5:]
            new_texture = f"m_Texture: {{fileID: 2800000, guid: {guid}, type: 3}}"
        else:
            raise ValueError(
                f"Texture value must be 'guid:<hex>' or empty, got: {value}",
            )
        # Within the named tex entry, replace the m_Texture line.
        pattern = re.compile(
            rf"(- {re.escape(property_name)}:\s*\n\s+)m_Texture:\s*\{{[^}}]+\}}",
        )
        return _replace_in_section(text, "m_TexEnvs", pattern, rf"\g<1>{new_texture}")

    raise ValueError(f"Unknown category: {category}")


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def write_material_property(
    target_path: str,
    property_name: str,
    value: str,
    *,
    dry_run: bool = True,
) -> dict:
    """Write a single property value in a ``.mat`` file.

    Args:
        target_path: Path to the .mat file.
        property_name: Property name (e.g. ``_Glossiness``).
        value: New value as string.  Format depends on the property type:

            * Float / Int: ``"0.5"`` or ``"128"``
            * Color: ``"[r, g, b, a]"``  (JSON array)
            * Texture: ``"guid:abc..."`` to set, or ``""`` to null-out.

        dry_run: If *True*, return a preview without writing.

    Returns:
        Envelope dict with ``success``, ``severity``, ``code``, ``message``,
        ``data``, ``diagnostics``.
    """
    path = Path(target_path)

    if path.suffix.lower() != ".mat":
        return _error_dict(
            "MAT_PROP_WRONG_EXT",
            f"Expected .mat file, got {path.suffix}",
        )

    if not path.exists():
        return _error_dict(
            "MAT_PROP_FILE_NOT_FOUND",
            f"File not found: {target_path}",
        )

    text = path.read_text(encoding="utf-8")

    category, before, section_name = _find_property(text, property_name)
    if category is None:
        all_names = _list_all_property_names(text)
        suggestions = suggest_similar(property_name, all_names)
        return _error_dict(
            "MAT_PROP_NOT_FOUND",
            f"Property '{property_name}' not found in {path.name}",
            data={"available_properties": all_names, "suggestions": suggestions},
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

    try:
        new_text = _replace_property(text, property_name, value, category)
    except ValueError as exc:
        return _error_dict("MAT_PROP_PARSE_ERROR", str(exc))

    path.write_text(new_text, encoding="utf-8")

    # Verify the write by re-parsing.
    verify_cat, _verify_val, _ = _find_property(new_text, property_name)
    if verify_cat is None:
        return _error_dict(
            "MAT_PROP_VERIFY_FAILED",
            "Property disappeared after write",
        )

    return _success_dict(
        "MAT_PROP_APPLIED",
        f"Changed {property_name} from {before} to {value}",
        data=data,
    )
