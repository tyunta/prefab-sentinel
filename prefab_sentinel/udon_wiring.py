"""MonoBehaviour field wiring inspector.

Parses Unity YAML text to extract MonoBehaviour serialized fields and
detects null references, internal fileID mismatches, and duplicate wiring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prefab_sentinel.contracts import Diagnostic, Severity, max_severity
from prefab_sentinel.unity_assets import REFERENCE_PATTERN, normalize_guid
from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_MONOBEHAVIOUR,
    GameObjectInfo,
    YamlBlock,
    parse_game_objects,
    split_yaml_blocks,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# VRChat SDK UdonBehaviour script GUID (com.vrchat.worlds, stable across SDK versions)
UDON_BEHAVIOUR_GUID = "45115577ef41a5b4ca741ed302693907"

SKIP_FIELDS = frozenset(
    {
        "m_ObjectHideFlags",
        "m_CorrespondingSourceObject",
        "m_PrefabInstance",
        "m_PrefabAsset",
        "m_GameObject",
        "m_Enabled",
        "m_EditorHideFlags",
        "m_Script",
        "m_Name",
        "m_EditorClassIdentifier",
    }
)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WiringField:
    name: str
    value: str
    line: int
    file_id: str
    guid: str
    is_overridden: bool = False


@dataclass(slots=True)
class ComponentWiring:
    file_id: str
    game_object_file_id: str
    script_guid: str
    fields: list[WiringField]
    block_start_line: int
    is_udon_sharp: bool
    backing_udon_file_id: str
    override_count: int = 0


@dataclass(slots=True)
class WiringResult:
    components: list[ComponentWiring]
    null_references: list[Diagnostic]
    duplicate_references: list[Diagnostic]
    internal_broken_refs: list[Diagnostic]
    max_severity: Severity
    game_objects: dict[str, GameObjectInfo]


# ---------------------------------------------------------------------------
# Parser functions
# ---------------------------------------------------------------------------


def _parse_monobehaviour_fields(block: YamlBlock) -> ComponentWiring | None:
    """Extract fields from a MonoBehaviour block.

    Returns None for non-MonoBehaviour blocks and for UdonBehaviour blocks
    (identified by m_Script GUID matching ``UDON_BEHAVIOUR_GUID``).
    """
    if block.class_id != CLASS_ID_MONOBEHAVIOUR:
        return None

    lines = block.text.split("\n")
    game_object_file_id = ""
    script_guid = ""
    is_udon_sharp = False
    backing_udon_file_id = ""
    fields: list[WiringField] = []
    current_field_name: str | None = None
    base_indent: int | None = None

    def _try_append_ref(name: str, value: str, line_num: int) -> None:
        ref_match = REFERENCE_PATTERN.search(value)
        if ref_match:
            fields.append(
                WiringField(
                    name=name,
                    value=value,
                    line=line_num,
                    file_id=ref_match.group(1),
                    guid=normalize_guid(ref_match.group(2) or ""),
                )
            )

    for line_offset, line in enumerate(lines):
        absolute_line = block.start_line + line_offset
        stripped = line.rstrip()
        if not stripped:
            continue
        current_indent = len(line) - len(line.lstrip())

        if base_indent is not None and current_indent > base_indent:
            array_match = re.match(r"^\s+-\s*(\{.*\})", line)
            if array_match and current_field_name:
                _try_append_ref(current_field_name, array_match.group(1).strip(), absolute_line)
            continue

        go_match = re.match(r"\s+m_GameObject:\s*\{fileID:\s*(-?\d+)", line)
        if go_match:
            game_object_file_id = go_match.group(1)
            if base_indent is None:
                base_indent = current_indent
            continue

        script_match = re.match(r"\s+m_Script:\s*\{.*?guid:\s*([0-9a-fA-F]{32})", line)
        if script_match:
            script_guid = normalize_guid(script_match.group(1))
            if base_indent is None:
                base_indent = current_indent
            continue

        udon_sharp_match = re.match(
            r"\s+_udonSharpBackingUdonBehaviour:\s*\{fileID:\s*(-?\d+)",
            line,
        )
        if udon_sharp_match:
            is_udon_sharp = True
            backing_udon_file_id = udon_sharp_match.group(1)
            continue

        field_match = re.match(r"^(\s+)(\w+):\s*(.*)", line)
        if field_match:
            if base_indent is None:
                base_indent = current_indent
            elif current_indent != base_indent:
                continue

            name = field_match.group(2)
            value = field_match.group(3).strip()

            if name in SKIP_FIELDS:
                current_field_name = None
                continue

            current_field_name = name
            _try_append_ref(name, value, absolute_line)
            continue

        array_match = re.match(r"^\s+-\s*(\{.*\})", line)
        if array_match and current_field_name:
            _try_append_ref(current_field_name, array_match.group(1).strip(), absolute_line)

    if script_guid == UDON_BEHAVIOUR_GUID:
        return None

    return ComponentWiring(
        file_id=block.file_id,
        game_object_file_id=game_object_file_id,
        script_guid=script_guid,
        fields=fields,
        block_start_line=block.start_line,
        is_udon_sharp=is_udon_sharp,
        backing_udon_file_id=backing_udon_file_id,
    )


# ---------------------------------------------------------------------------
# Field name extraction (all fields, not just references)
# ---------------------------------------------------------------------------


def extract_monobehaviour_field_names(block: YamlBlock) -> list[str]:
    """Extract all top-level field names from a MonoBehaviour YAML block.

    Unlike :func:`_parse_monobehaviour_fields` which only captures reference
    fields (fileID/GUID patterns), this returns ALL field names including
    plain scalar values like ``speed: 5.0``.

    Unity built-in fields in :data:`SKIP_FIELDS` are excluded.
    Returns an empty list for non-MonoBehaviour blocks.
    """
    if block.class_id != CLASS_ID_MONOBEHAVIOUR:
        return []

    lines = block.text.split("\n")
    field_names: list[str] = []
    base_indent: int | None = None

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            continue
        current_indent = len(line) - len(line.lstrip())

        # Skip nested lines (array elements, sub-properties)
        if base_indent is not None and current_indent > base_indent:
            continue

        field_match = re.match(r"^(\s+)(\w+):\s*(.*)", line)
        if not field_match:
            continue

        if base_indent is None:
            base_indent = current_indent
        elif current_indent != base_indent:
            continue

        name = field_match.group(2)
        if name in SKIP_FIELDS:
            continue

        field_names.append(name)

    return field_names


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyze_wiring(
    text: str,
    file_path: str,
    *,
    udon_only: bool = False,
    override_map: dict[str, set[str]] | None = None,
) -> WiringResult:
    """Analyze MonoBehaviour field wiring in a Unity YAML file.

    Args:
        text: Raw Unity YAML content.
        file_path: Path to the source file (used in diagnostics).
        udon_only: When ``True``, only report UdonSharp components.
        override_map: Optional mapping of component fileID → set of
            overridden property paths (used for Variant annotation).
    """
    blocks = split_yaml_blocks(text)
    local_file_ids = {block.file_id for block in blocks}
    game_objects = parse_game_objects(blocks)

    components: list[ComponentWiring] = []
    for block in blocks:
        parsed = _parse_monobehaviour_fields(block)
        if parsed is None:
            continue
        if udon_only and not parsed.is_udon_sharp:
            continue
        # Annotate override information from Variant
        if override_map is not None:
            ov_paths = override_map.get(parsed.file_id, set())
            parsed.override_count = len(ov_paths)
            for field in parsed.fields:
                field.is_overridden = any(
                    pp == field.name or pp.startswith(field.name + ".")
                    for pp in ov_paths
                )
        components.append(parsed)

    if not components:
        return WiringResult(
            components=[],
            null_references=[],
            duplicate_references=[],
            internal_broken_refs=[],
            max_severity=Severity.INFO,
            game_objects=game_objects,
        )

    null_references: list[Diagnostic] = []
    internal_broken: list[Diagnostic] = []
    ref_targets: dict[tuple[str, str], list[tuple[str, str]]] = {}
    severities: list[Severity] = []

    def _go_name(comp: ComponentWiring) -> str:
        go = game_objects.get(comp.game_object_file_id)
        if go and go.name:
            return go.name
        if comp.game_object_file_id:
            return f"fileID:{comp.game_object_file_id}"
        return "<unknown>"

    for comp in components:
        for f in comp.fields:
            if f.file_id == "0" and not f.guid:
                null_references.append(
                    Diagnostic(
                        path=file_path,
                        location=f"line {f.line}",
                        detail=f"Null reference: {_go_name(comp)}.{f.name}",
                        evidence=f.value,
                    )
                )
                severities.append(Severity.WARNING)
                continue

            if f.file_id != "0" and not f.guid and f.file_id not in local_file_ids:
                internal_broken.append(
                    Diagnostic(
                        path=file_path,
                        location=f"line {f.line}",
                        detail=f"Internal fileID not found: {_go_name(comp)}.{f.name} -> fileID:{f.file_id}",
                        evidence=f.value,
                    )
                )
                severities.append(Severity.ERROR)
                continue

            if f.file_id != "0":
                key = (f.file_id, f.guid)
                ref_targets.setdefault(key, []).append((comp.file_id, f.name))

    duplicate_refs: list[Diagnostic] = []
    for (target_fid, _target_guid), sources in ref_targets.items():
        if len(sources) < 2:
            continue
        # Group by component to distinguish same-component vs cross-component
        by_component: dict[str, list[str]] = {}
        for cid, fname in sources:
            by_component.setdefault(cid, []).append(fname)

        source_labels = [f"fileID:{cid}.{fname}" for cid, fname in sources]
        has_same_component = any(len(fields) >= 2 for fields in by_component.values())
        is_cross_component = len(by_component) >= 2

        if has_same_component:
            duplicate_refs.append(
                Diagnostic(
                    path=file_path,
                    location=f"fileID:{target_fid}",
                    detail=f"[same-component] Duplicate reference target from {len(sources)} fields: {', '.join(source_labels)}",
                    evidence=f"{{fileID: {target_fid}}}",
                )
            )
            severities.append(Severity.WARNING)
        if is_cross_component:
            duplicate_refs.append(
                Diagnostic(
                    path=file_path,
                    location=f"fileID:{target_fid}",
                    detail=f"[cross-component] Duplicate reference target from {len(sources)} fields: {', '.join(source_labels)}",
                    evidence=f"{{fileID: {target_fid}}}",
                )
            )
            severities.append(Severity.INFO)

    overall = max_severity(severities) if severities else Severity.INFO

    return WiringResult(
        components=components,
        null_references=null_references,
        duplicate_references=duplicate_refs,
        internal_broken_refs=internal_broken,
        max_severity=overall,
        game_objects=game_objects,
    )
