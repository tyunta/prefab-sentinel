"""MonoBehaviour field wiring inspector.

Parses Unity YAML text to extract MonoBehaviour serialized fields and
detects null references, internal fileID mismatches, and duplicate wiring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prefab_sentinel.contracts import Diagnostic, Severity, max_severity
from prefab_sentinel.unity_assets import REFERENCE_PATTERN

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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

DOCUMENT_HEADER_PATTERN = re.compile(r"^--- !u!(\d+) &(-?\d+)", re.MULTILINE)

# class_id for MonoBehaviour = 114, GameObject = 1
_MONOBEHAVIOUR_CLASS_ID = "114"
_GAMEOBJECT_CLASS_ID = "1"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class YamlBlock:
    class_id: str
    file_id: str
    text: str
    start_line: int


@dataclass(slots=True)
class WiringField:
    name: str
    value: str
    line: int
    file_id: str
    guid: str


@dataclass(slots=True)
class ComponentWiring:
    file_id: str
    game_object_file_id: str
    script_guid: str
    fields: list[WiringField]
    block_start_line: int
    is_udon_sharp: bool
    backing_udon_file_id: str


@dataclass(slots=True)
class GameObjectInfo:
    file_id: str
    name: str
    component_file_ids: list[str]


@dataclass(slots=True)
class WiringResult:
    components: list[ComponentWiring]
    null_references: list[Diagnostic]
    duplicate_references: list[Diagnostic]
    internal_broken_refs: list[Diagnostic]
    max_severity: Severity


# ---------------------------------------------------------------------------
# Parser functions
# ---------------------------------------------------------------------------


def split_yaml_blocks(text: str) -> list[YamlBlock]:
    """Split Unity YAML text into per-document blocks."""
    if not text.strip():
        return []

    headers: list[tuple[int, str, str, int]] = []
    for match in DOCUMENT_HEADER_PATTERN.finditer(text):
        # Count newlines before this match to find the line number
        line_number = text.count("\n", 0, match.start()) + 1
        headers.append((match.start(), match.group(1), match.group(2), line_number))

    blocks: list[YamlBlock] = []
    for i, (start, class_id, file_id, start_line) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        block_text = text[start:end]
        blocks.append(
            YamlBlock(class_id=class_id, file_id=file_id, text=block_text, start_line=start_line)
        )

    return blocks


def _parse_monobehaviour_fields(block: YamlBlock) -> ComponentWiring | None:
    """Extract fields from a MonoBehaviour block.

    Returns None for non-MonoBehaviour blocks and for UdonBehaviour blocks
    (identified by m_Script GUID matching ``UDON_BEHAVIOUR_GUID``).
    """
    if block.class_id != _MONOBEHAVIOUR_CLASS_ID:
        return None

    lines = block.text.split("\n")
    game_object_file_id = ""
    script_guid = ""
    is_udon_sharp = False
    backing_udon_file_id = ""
    fields: list[WiringField] = []
    current_field_name: str | None = None
    # Detect the base indent of MonoBehaviour fields (set from first key line)
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
                    guid=(ref_match.group(2) or "").lower(),
                )
            )

    for line_offset, line in enumerate(lines):
        absolute_line = block.start_line + line_offset
        stripped = line.rstrip()
        if not stripped:
            continue
        current_indent = len(line) - len(line.lstrip())

        # Once we know the base indent, skip lines deeper than base level
        # (nested struct children), but allow array elements which use "-"
        if base_indent is not None and current_indent > base_indent:
            array_match = re.match(r"^\s+-\s*(\{.*\})", line)
            if array_match and current_field_name:
                _try_append_ref(current_field_name, array_match.group(1).strip(), absolute_line)
            continue

        # Parse m_GameObject reference
        go_match = re.match(r"\s+m_GameObject:\s*\{fileID:\s*(-?\d+)", line)
        if go_match:
            game_object_file_id = go_match.group(1)
            if base_indent is None:
                base_indent = current_indent
            continue

        # Parse m_Script reference for GUID
        script_match = re.match(r"\s+m_Script:\s*\{.*?guid:\s*([0-9a-fA-F]{32})", line)
        if script_match:
            script_guid = script_match.group(1).lower()
            if base_indent is None:
                base_indent = current_indent
            continue

        # Detect UdonSharp marker
        udon_sharp_match = re.match(
            r"\s+_udonSharpBackingUdonBehaviour:\s*\{fileID:\s*(-?\d+)",
            line,
        )
        if udon_sharp_match:
            is_udon_sharp = True
            backing_udon_file_id = udon_sharp_match.group(1)
            continue

        # Top-level field line
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

        # Standalone array element at base indent
        array_match = re.match(r"^\s+-\s*(\{.*\})", line)
        if array_match and current_field_name:
            _try_append_ref(current_field_name, array_match.group(1).strip(), absolute_line)

    # Exclude UdonBehaviour blocks (runtime companion, not the UdonSharp proxy)
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


def _parse_game_objects(blocks: list[YamlBlock]) -> dict[str, GameObjectInfo]:
    """Extract GameObject name and component list from blocks."""
    result: dict[str, GameObjectInfo] = {}
    for block in blocks:
        if block.class_id != _GAMEOBJECT_CLASS_ID:
            continue
        name = ""
        component_file_ids: list[str] = []
        for line in block.text.split("\n"):
            name_match = re.match(r"\s+m_Name:\s*(.*)", line)
            if name_match:
                name = name_match.group(1).strip()
                continue
            comp_match = re.match(r"\s+-\s*component:\s*\{fileID:\s*(-?\d+)\}", line)
            if comp_match:
                component_file_ids.append(comp_match.group(1))
        result[block.file_id] = GameObjectInfo(
            file_id=block.file_id,
            name=name,
            component_file_ids=component_file_ids,
        )
    return result


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyze_wiring(
    text: str,
    file_path: str,
    *,
    udon_only: bool = False,
) -> WiringResult:
    """Analyze MonoBehaviour field wiring in a Unity YAML file.

    Returns a ``WiringResult`` containing components, diagnostics for null
    references, internal broken refs, duplicate references, and the overall
    max severity.
    """
    blocks = split_yaml_blocks(text)
    local_file_ids = {block.file_id for block in blocks}
    game_objects = _parse_game_objects(blocks)

    components: list[ComponentWiring] = []
    for block in blocks:
        parsed = _parse_monobehaviour_fields(block)
        if parsed is None:
            continue
        if udon_only and not parsed.is_udon_sharp:
            continue
        components.append(parsed)

    if not components:
        return WiringResult(
            components=[],
            null_references=[],
            duplicate_references=[],
            internal_broken_refs=[],
            max_severity=Severity.INFO,
        )

    null_references: list[Diagnostic] = []
    internal_broken: list[Diagnostic] = []
    # Track (file_id, guid) -> list of (component_file_id, field_name) for duplicate detection
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
            # null reference: fileID == 0 and no external GUID
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

            # internal fileID mismatch: local reference whose target is missing
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

            # Track for duplicate detection (only local references with fileID != 0)
            if f.file_id != "0":
                key = (f.file_id, f.guid)
                ref_targets.setdefault(key, []).append((comp.file_id, f.name))

    # Duplicate reference detection
    duplicate_refs: list[Diagnostic] = []
    for (target_fid, _target_guid), sources in ref_targets.items():
        if len(sources) < 2:
            continue
        source_labels = [f"fileID:{cid}.{fname}" for cid, fname in sources]
        duplicate_refs.append(
            Diagnostic(
                path=file_path,
                location=f"fileID:{target_fid}",
                detail=f"Duplicate reference target from {len(sources)} fields: {', '.join(source_labels)}",
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
    )
