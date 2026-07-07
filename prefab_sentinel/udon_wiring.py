"""MonoBehaviour field wiring inspector.

Parses Unity YAML text to extract MonoBehaviour serialized fields and
detects null references, internal fileID mismatches, and duplicate wiring.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from prefab_sentinel.contracts import Diagnostic, Severity, max_severity
from prefab_sentinel.udon_wiring_parser import parse_monobehaviour_fields
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
class NullFieldClassification:
    """Issue #296 — cause-of-null category for a single null reference field.

    ``kind`` is one of the documented three-member vocabulary:

    * ``unwired``               — neither override-map nor external-GUID
                                  signal is present; the field was
                                  never wired.
    * ``variant_overridden_null`` — the Variant override map carries the
                                  field path on the component; the
                                  Variant intentionally clears the
                                  inherited value.
    * ``dangling``              — the field carries a non-empty external
                                  GUID with an unresolved local id
                                  (``fileID: 0`` accompanies the GUID);
                                  the reference target was deleted or
                                  moved out of scope.

    ``evidence`` is a short textual rationale naming the signal that
    determined the category so a reader can confirm the classification
    without re-running the analyzer.
    """

    name: str
    kind: str
    evidence: str


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
    null_field_names: list[str] = field(default_factory=list)
    null_field_classifications: list[NullFieldClassification] = field(
        default_factory=list,
    )


@dataclass(slots=True)
class WiringResult:
    components: list[ComponentWiring]
    null_references: list[Diagnostic]
    duplicate_references: list[Diagnostic]
    internal_broken_refs: list[Diagnostic]
    max_severity: Severity
    game_objects: dict[str, GameObjectInfo]


# ---------------------------------------------------------------------------
# Field name extraction (all fields, not just references)
# ---------------------------------------------------------------------------


def extract_monobehaviour_field_names(block: YamlBlock) -> list[str]:
    """Extract all top-level field names from a MonoBehaviour YAML block.

    Unlike :func:`parse_monobehaviour_fields` which only captures reference
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
    blocks: Sequence[YamlBlock] | None = None,
) -> WiringResult:
    """Analyze MonoBehaviour field wiring in a Unity YAML file.

    Args:
        text: Raw Unity YAML content.
        file_path: Path to the source file (used in diagnostics).
        udon_only: When ``True``, only report UdonSharp components.
        override_map: Optional mapping of component fileID → set of
            overridden property paths (used for Variant annotation).
        blocks: Optional pre-parsed YAML blocks for cached nested Prefabs.
    """
    parsed_blocks = list(blocks) if blocks is not None else split_yaml_blocks(text)
    local_file_ids = {block.file_id for block in parsed_blocks}
    game_objects = parse_game_objects(parsed_blocks)

    components: list[ComponentWiring] = []
    for block in parsed_blocks:
        parsed = parse_monobehaviour_fields(block)
        if parsed is None:
            continue
        if udon_only and not parsed.is_udon_sharp:
            continue
        # Annotate override information from Variant
        if override_map is not None:
            ov_paths = override_map.get(parsed.file_id, set())
            parsed.override_count = len(ov_paths)
            for wiring_field in parsed.fields:
                wiring_field.is_overridden = any(
                    pp == wiring_field.name or pp.startswith(wiring_field.name + ".")
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
            # Issue #296: null reference partitions into three causes —
            # variant-override clears the inherited value (precedence
            # over unwired so an override on a null field is recognised
            # as intentional); dangling external GUID indicates a moved
            # or deleted asset; unwired is the residual when neither
            # signal is present.
            if f.file_id == "0" and f.guid:
                comp.null_field_names.append(f.name)
                comp.null_field_classifications.append(
                    NullFieldClassification(
                        name=f.name,
                        kind="dangling",
                        evidence=(
                            f"external guid {f.guid} present with "
                            f"local fileID 0 — referenced asset is "
                            f"unresolved"
                        ),
                    )
                )
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

            if f.file_id == "0" and not f.guid:
                comp.null_field_names.append(f.name)
                if f.is_overridden:
                    classification = NullFieldClassification(
                        name=f.name,
                        kind="variant_overridden_null",
                        evidence=(
                            "field appears in the Variant override "
                            "map; null value is intentional"
                        ),
                    )
                else:
                    classification = NullFieldClassification(
                        name=f.name,
                        kind="unwired",
                        evidence=(
                            "no override or external guid signal — "
                            "field was never wired"
                        ),
                    )
                comp.null_field_classifications.append(classification)
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
