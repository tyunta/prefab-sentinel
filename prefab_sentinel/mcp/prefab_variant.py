from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse
from prefab_sentinel.unity_assets import (
    SOURCE_PREFAB_PATTERN,
    collect_project_guid_index,
    decode_text_file,
    find_project_root,
    normalize_guid,
    resolve_scope_path,
)
from prefab_sentinel.unity_yaml_parser import split_yaml_blocks

OVERRIDE_TARGET_PATTERN = re.compile(
    r"target:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?(?:,\s*type:\s*(-?\d+))?\}"
)
ARRAY_SIZE_PATH_PATTERN = re.compile(r"^(?P<prefix>.+)\.Array\.size$")
ARRAY_DATA_PATH_PATTERN = re.compile(r"^(?P<prefix>.+)\.Array\.data\[(?P<index>\d+)\]$")


@dataclass(slots=True)
class OverrideEntry:
    target_file_id: str
    target_guid: str
    target_type: str | None
    target_raw: str
    property_path: str
    value: str
    object_reference: str
    line: int

    @property
    def target_key(self) -> str:
        return f"{self.target_guid}:{self.target_file_id}"


@dataclass(slots=True)
class ChainValue:
    """A resolved property value with origin tracking."""

    target_file_id: str
    property_path: str
    value: str
    origin_path: str  # relative path of the Prefab that set this value
    origin_depth: int  # 0 = the variant itself, 1 = parent, ...


class PrefabVariantMcp:
    """Read-only prefab variant MCP interface for Phase 1."""

    TOOL_NAME = "prefab-variant-mcp"

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = find_project_root(project_root or Path.cwd())
        self._guid_index: dict[str, Path] | None = None

    def _guid_map(self) -> dict[str, Path]:
        if self._guid_index is None:
            self._guid_index = collect_project_guid_index(self.project_root)
        return self._guid_index

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def _load_variant(self, variant_path: str) -> tuple[Path | None, str | None, ToolResponse | None]:
        path = resolve_scope_path(variant_path, self.project_root)
        if not path.exists():
            return (
                None,
                None,
                ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="PVR404",
                    message="Variant path does not exist.",
                    data={"variant_path": variant_path, "read_only": True},
                    diagnostics=[],
                ),
            )
        try:
            text = decode_text_file(path)
        except UnicodeDecodeError:
            return (
                None,
                None,
                ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="PVR400",
                    message="Variant file could not be decoded as UTF-8/CP932.",
                    data={"variant_path": variant_path, "read_only": True},
                    diagnostics=[],
                ),
            )
        return path, text, None

    def _parse_overrides(self, text: str) -> list[OverrideEntry]:
        lines = text.splitlines()
        entries: list[OverrideEntry] = []
        in_modifications = False
        mod_indent = 0
        current: OverrideEntry | None = None

        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))

            if stripped.endswith("m_Modifications:"):
                in_modifications = True
                mod_indent = indent
                if current is not None:
                    entries.append(current)
                    current = None
                continue

            if in_modifications and stripped and indent <= mod_indent and not stripped.startswith("-"):
                in_modifications = False
                if current is not None:
                    entries.append(current)
                    current = None

            if not in_modifications:
                continue

            if stripped.startswith("- target:") or stripped.startswith("target:"):
                if current is not None:
                    entries.append(current)
                target_match = OVERRIDE_TARGET_PATTERN.search(stripped)
                target_file_id = ""
                target_guid = ""
                target_type: str | None = None
                if target_match:
                    target_file_id = target_match.group(1)
                    target_guid = normalize_guid(target_match.group(2) or "")
                    target_type = target_match.group(3)
                current = OverrideEntry(
                    target_file_id=target_file_id,
                    target_guid=target_guid,
                    target_type=target_type,
                    target_raw=stripped.split("target:", 1)[-1].strip(),
                    property_path="",
                    value="",
                    object_reference="",
                    line=index,
                )
                continue

            if current is None:
                continue

            if stripped.startswith("propertyPath:"):
                current.property_path = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("value:"):
                current.value = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("objectReference:"):
                current.object_reference = stripped.split(":", 1)[1].strip()

        if current is not None:
            entries.append(current)

        return entries

    def resolve_prefab_chain(self, variant_path: str) -> ToolResponse:
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        assert path is not None
        assert text is not None

        chain = [{"path": self._relative(path), "guid": None}]
        diagnostics: list[Diagnostic] = []
        visited_paths = {path.resolve()}
        current_text = text
        depth_limit = 12

        for _ in range(depth_limit):
            source = SOURCE_PREFAB_PATTERN.search(current_text)
            if source is None:
                break
            source_guid = normalize_guid(source.group(2))
            target = self._guid_map().get(source_guid)
            if target is None:
                diagnostics.append(
                    Diagnostic(
                        path=self._relative(path),
                        location="m_SourcePrefab",
                        detail="missing_asset",
                        evidence=f"guid {source_guid} from source prefab is not found",
                    )
                )
                chain.append({"path": "", "guid": source_guid})
                break
            chain.append({"path": self._relative(target), "guid": source_guid})
            if target.resolve() in visited_paths:
                diagnostics.append(
                    Diagnostic(
                        path=self._relative(target),
                        location="prefab_chain",
                        detail="loop_detected",
                        evidence="prefab source chain references an already visited asset",
                    )
                )
                break
            visited_paths.add(target.resolve())
            try:
                current_text = decode_text_file(target)
            except UnicodeDecodeError:
                diagnostics.append(
                    Diagnostic(
                        path=self._relative(target),
                        location="file",
                        detail="unreadable_file",
                        evidence="unable to decode source prefab",
                    )
                )
                break
        else:
            diagnostics.append(
                Diagnostic(
                    path=self._relative(path),
                    location="prefab_chain",
                    detail="depth_limit",
                    evidence=f"chain depth exceeded {depth_limit}",
                )
            )

        if diagnostics:
            severity = Severity.WARNING
            code = "PVR_CHAIN_WARN"
            message = "Prefab chain resolved with warnings."
        else:
            severity = Severity.INFO
            code = "PVR_CHAIN_OK"
            message = "Prefab chain resolved."

        return ToolResponse(
            success=True,
            severity=severity,
            code=code,
            message=message,
            data={"variant_path": self._relative(path), "chain": chain, "read_only": True},
            diagnostics=diagnostics,
        )

    def list_overrides(
        self,
        variant_path: str,
        component_filter: str | None = None,
    ) -> ToolResponse:
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        assert path is not None
        assert text is not None

        entries = self._parse_overrides(text)
        filtered = entries
        if component_filter:
            needle = component_filter.lower()
            filtered = [
                entry
                for entry in entries
                if needle in entry.target_raw.lower() or needle in entry.property_path.lower()
            ]

        payload = [
            {
                "line": entry.line,
                "target_file_id": entry.target_file_id,
                "target_guid": entry.target_guid,
                "property_path": entry.property_path,
                "value": entry.value,
                "object_reference": entry.object_reference,
            }
            for entry in filtered
        ]
        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="PVR_OVERRIDES_OK",
            message="Override list extracted.",
            data={
                "variant_path": self._relative(path),
                "component_filter": component_filter,
                "override_count": len(filtered),
                "overrides": payload,
                "read_only": True,
            },
            diagnostics=[],
        )

    def compute_effective_values(
        self,
        variant_path: str,
        component_filter: str | None = None,
    ) -> ToolResponse:
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        assert path is not None
        assert text is not None

        entries = self._parse_overrides(text)
        if component_filter:
            needle = component_filter.lower()
            entries = [
                entry
                for entry in entries
                if needle in entry.target_raw.lower() or needle in entry.property_path.lower()
            ]

        effective: dict[str, dict[str, str]] = {}
        for entry in entries:
            if not entry.property_path:
                continue
            key = f"{entry.target_key}:{entry.property_path}"
            effective[key] = {
                "target_key": entry.target_key,
                "target_guid": entry.target_guid,
                "target_file_id": entry.target_file_id,
                "property_path": entry.property_path,
                "value": entry.value,
                "object_reference": entry.object_reference,
                "line": str(entry.line),
            }

        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="PVR_EFFECTIVE_OK",
            message="Effective override values computed by last-write-wins rule.",
            data={
                "variant_path": self._relative(path),
                "component_filter": component_filter,
                "value_count": len(effective),
                "effective_values": list(effective.values()),
                "read_only": True,
            },
            diagnostics=[],
        )

    def detect_stale_overrides(self, variant_path: str) -> ToolResponse:
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        assert path is not None
        assert text is not None

        entries = self._parse_overrides(text)
        diagnostics: list[Diagnostic] = []
        key_count: dict[tuple[str, str], list[int]] = defaultdict(list)
        array_sizes: dict[tuple[str, str], int] = {}
        array_max_indexes: dict[tuple[str, str], int] = {}

        for entry in entries:
            key = (entry.target_key, entry.property_path)
            if entry.property_path:
                key_count[key].append(entry.line)
            else:
                diagnostics.append(
                    Diagnostic(
                        path=self._relative(path),
                        location=f"{entry.line}:1",
                        detail="empty_property_path",
                        evidence="override entry does not specify propertyPath",
                    )
                )

            size_match = ARRAY_SIZE_PATH_PATTERN.match(entry.property_path)
            if size_match:
                prefix = size_match.group("prefix")
                try:
                    size = int(entry.value)
                except ValueError:
                    continue
                array_sizes[(entry.target_key, prefix)] = size

            data_match = ARRAY_DATA_PATH_PATTERN.match(entry.property_path)
            if data_match:
                prefix = data_match.group("prefix")
                index = int(data_match.group("index"))
                key_array = (entry.target_key, prefix)
                current = array_max_indexes.get(key_array, -1)
                if index > current:
                    array_max_indexes[key_array] = index

        for (target_key, property_path), lines in key_count.items():
            if len(lines) > 1:
                diagnostics.append(
                    Diagnostic(
                        path=self._relative(path),
                        location=f"{lines[-1]}:1",
                        detail="duplicate_override",
                        evidence=(
                            f"{target_key} / {property_path} appears {len(lines)} times; "
                            "later entries shadow earlier entries"
                        ),
                    )
                )

        for key, max_index in array_max_indexes.items():
            size = array_sizes.get(key)
            if size is None:
                continue
            if max_index >= size:
                target_key, prefix = key
                diagnostics.append(
                    Diagnostic(
                        path=self._relative(path),
                        location="array_override",
                        detail="array_size_mismatch",
                        evidence=(
                            f"{target_key} / {prefix}: size={size} but data index {max_index} exists"
                        ),
                    )
                )

        if diagnostics:
            return ToolResponse(
                success=False,
                severity=Severity.WARNING,
                code="PVR001",
                message="Potential stale overrides detected.",
                data={
                    "variant_path": self._relative(path),
                    "stale_count": len(diagnostics),
                    "read_only": True,
                },
                diagnostics=diagnostics,
            )

        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="PVR_STALE_NONE",
            message="No stale override patterns detected.",
            data={"variant_path": self._relative(path), "stale_count": 0, "read_only": True},
            diagnostics=[],
        )

    # ------------------------------------------------------------------
    # Chain-aware before-value resolution
    # ------------------------------------------------------------------

    def resolve_chain_values(
        self,
        variant_path: str,
    ) -> dict[str, str]:
        """Walk the full Variant chain and return effective override values.

        Returns a dict keyed by ``"{target_file_id}:{property_path}"`` with
        the effective value (last-write-wins from the *bottom* of the chain).
        The chain is traversed from the target variant up to the base prefab.

        At each level the ``m_Modifications`` overrides are collected.  Values
        found in a *closer* (child) variant shadow those from parents.  At the
        base prefab, property values are extracted directly from the YAML
        component blocks on a best-effort basis.
        """
        path = resolve_scope_path(variant_path, self.project_root)
        if not path.exists():
            return {}

        try:
            text = decode_text_file(path)
        except (OSError, UnicodeDecodeError):
            return {}

        if SOURCE_PREFAB_PATTERN.search(text) is None:
            # Not a variant – nothing to resolve
            return {}

        # Collect overrides bottom-up.  Child overrides take precedence.
        result: dict[str, str] = {}
        visited: set[str] = set()
        current_text: str | None = text
        depth_limit = 12

        for _ in range(depth_limit):
            if current_text is None:
                break

            # Parse overrides at this level and merge (child wins)
            entries = self._parse_overrides(current_text)
            for entry in entries:
                if not entry.property_path:
                    continue
                key = f"{entry.target_file_id}:{entry.property_path}"
                if key not in result:
                    obj_ref = entry.object_reference
                    val = entry.value
                    result[key] = (
                        obj_ref if obj_ref and obj_ref != "{fileID: 0}" else val
                    )

            # Follow m_SourcePrefab to the parent
            source = SOURCE_PREFAB_PATTERN.search(current_text)
            if source is None:
                break

            source_guid = normalize_guid(source.group(2))
            if source_guid in visited:
                break
            visited.add(source_guid)

            target_file = self._guid_map().get(source_guid)
            if target_file is None:
                break

            try:
                current_text = decode_text_file(target_file)
            except (OSError, UnicodeDecodeError):
                break

            # If the parent is itself a variant, continue the loop.
            # If it is a base prefab (no m_SourcePrefab), extract property
            # values directly from the YAML blocks and stop.
            if SOURCE_PREFAB_PATTERN.search(current_text) is None:
                self._merge_base_prefab_values(current_text, result)
                break

        return result

    def _merge_base_prefab_values(
        self,
        base_text: str,
        result: dict[str, str],
    ) -> None:
        """Best-effort extraction of property values from a base prefab.

        For each YAML block (keyed by fileID), scan for simple ``key: value``
        lines and Unity-style inline references
        ``{fileID: N, guid: G, type: T}``.  Array elements written as
        ``m_Foo.Array.data[N]`` are mapped from the YAML list syntax.
        """
        blocks = split_yaml_blocks(base_text)
        for block in blocks:
            file_id = block.file_id
            lines = block.text.split("\n")
            # Track array context for m_Foo list items
            current_array_field: str | None = None
            array_index = 0

            for line in lines:
                stripped = line.strip()

                # Skip document header and blank lines
                if stripped.startswith("---") or not stripped:
                    current_array_field = None
                    continue

                # Detect list items under an array field
                if stripped.startswith("- ") and current_array_field is not None:
                    item_value = stripped[2:].strip()
                    prop_path = f"{current_array_field}.Array.data[{array_index}]"
                    key = f"{file_id}:{prop_path}"
                    if key not in result:
                        result[key] = item_value
                    array_index += 1
                    continue

                # Reset array context on non-list line
                if not stripped.startswith("- "):
                    current_array_field = None

                # Simple "field: value" lines
                if ":" in stripped:
                    field_part, _, value_part = stripped.partition(":")
                    field_name = field_part.strip()
                    value_raw = value_part.strip()

                    # Skip the class name line (e.g. "MeshRenderer:")
                    if not value_raw and not field_name.startswith("m_"):
                        continue

                    # Detect start of a YAML list (field with no inline value
                    # or empty value, followed by "- " items)
                    if not value_raw and field_name.startswith("m_"):
                        current_array_field = field_name
                        array_index = 0
                        continue

                    # Skip empty "[]" arrays
                    if value_raw == "[]":
                        continue

                    key = f"{file_id}:{field_name}"
                    if key not in result:
                        result[key] = value_raw

    def resolve_chain_values_with_origin(
        self,
        variant_path: str,
    ) -> ToolResponse:
        """Walk the full Variant chain and return values with origin annotations.

        Like :meth:`resolve_chain_values` but each value carries the relative
        path and depth of the Prefab that set it.  ``origin_depth=0`` is the
        variant itself, ``1`` its parent, and so on up to the base prefab.
        """
        path = resolve_scope_path(variant_path, self.project_root)
        if not path.exists():
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="PVR404",
                message="Variant path does not exist.",
                data={"variant_path": variant_path, "read_only": True},
                diagnostics=[],
            )

        try:
            text = decode_text_file(path)
        except (OSError, UnicodeDecodeError):
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="PVR_READ_ERROR",
                message=f"Failed to read variant file: {variant_path}",
                data={"variant_path": variant_path, "read_only": True},
                diagnostics=[],
            )

        if SOURCE_PREFAB_PATTERN.search(text) is None:
            return ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="PVR_NOT_VARIANT",
                message="File is not a Variant; no chain to resolve.",
                data={
                    "variant_path": variant_path,
                    "chain": [],
                    "value_count": 0,
                    "values": [],
                    "read_only": True,
                },
                diagnostics=[],
            )

        result: dict[str, ChainValue] = {}
        chain: list[dict[str, object]] = []
        visited: set[str] = set()
        current_text: str | None = text
        current_path: Path = path
        depth = 0
        depth_limit = 12

        for _ in range(depth_limit):
            if current_text is None:
                break

            rel = self._relative(current_path)
            chain.append({"path": rel, "depth": depth})

            entries = self._parse_overrides(current_text)
            for entry in entries:
                if not entry.property_path:
                    continue
                key = f"{entry.target_file_id}:{entry.property_path}"
                if key not in result:
                    obj_ref = entry.object_reference
                    val = entry.value
                    effective = (
                        obj_ref if obj_ref and obj_ref != "{fileID: 0}" else val
                    )
                    result[key] = ChainValue(
                        target_file_id=entry.target_file_id,
                        property_path=entry.property_path,
                        value=effective,
                        origin_path=rel,
                        origin_depth=depth,
                    )

            source = SOURCE_PREFAB_PATTERN.search(current_text)
            if source is None:
                break

            source_guid = normalize_guid(source.group(2))
            if source_guid in visited:
                break
            visited.add(source_guid)

            target_file = self._guid_map().get(source_guid)
            if target_file is None:
                break

            try:
                current_text = decode_text_file(target_file)
            except (OSError, UnicodeDecodeError):
                break

            depth += 1
            current_path = target_file

            if SOURCE_PREFAB_PATTERN.search(current_text) is None:
                # Base prefab — extract values with origin tracking
                base_rel = self._relative(current_path)
                chain.append({"path": base_rel, "depth": depth})
                self._merge_base_values_with_origin(
                    current_text, result, base_rel, depth,
                )
                break

        values_list = [
            {
                "target_file_id": cv.target_file_id,
                "property_path": cv.property_path,
                "value": cv.value,
                "origin_path": cv.origin_path,
                "origin_depth": cv.origin_depth,
            }
            for cv in result.values()
        ]

        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="PVR_CHAIN_VALUES_WITH_ORIGIN",
            message=f"Resolved {len(values_list)} values across {len(chain)} chain levels.",
            data={
                "variant_path": variant_path,
                "chain": chain,
                "value_count": len(values_list),
                "values": values_list,
                "read_only": True,
            },
            diagnostics=[],
        )

    def _merge_base_values_with_origin(
        self,
        base_text: str,
        result: dict[str, ChainValue],
        origin_path: str,
        origin_depth: int,
    ) -> None:
        """Extract base prefab values with origin tracking.

        Same logic as :meth:`_merge_base_prefab_values` but stores
        :class:`ChainValue` instances instead of plain strings.
        """
        blocks = split_yaml_blocks(base_text)
        for block in blocks:
            file_id = block.file_id
            lines = block.text.split("\n")
            current_array_field: str | None = None
            array_index = 0

            for line in lines:
                stripped = line.strip()

                if stripped.startswith("---") or not stripped:
                    current_array_field = None
                    continue

                if stripped.startswith("- ") and current_array_field is not None:
                    item_value = stripped[2:].strip()
                    prop_path = f"{current_array_field}.Array.data[{array_index}]"
                    key = f"{file_id}:{prop_path}"
                    if key not in result:
                        result[key] = ChainValue(
                            target_file_id=file_id,
                            property_path=prop_path,
                            value=item_value,
                            origin_path=origin_path,
                            origin_depth=origin_depth,
                        )
                    array_index += 1
                    continue

                if not stripped.startswith("- "):
                    current_array_field = None

                if ":" in stripped:
                    field_part, _, value_part = stripped.partition(":")
                    field_name = field_part.strip()
                    value_raw = value_part.strip()

                    if not value_raw and not field_name.startswith("m_"):
                        continue

                    if not value_raw and field_name.startswith("m_"):
                        current_array_field = field_name
                        array_index = 0
                        continue

                    if value_raw == "[]":
                        continue

                    key = f"{file_id}:{field_name}"
                    if key not in result:
                        result[key] = ChainValue(
                            target_file_id=file_id,
                            property_path=field_name,
                            value=value_raw,
                            origin_path=origin_path,
                            origin_depth=origin_depth,
                        )
