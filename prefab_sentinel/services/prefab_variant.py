from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response, success_response
from prefab_sentinel.unity_assets import (
    SOURCE_PREFAB_PATTERN,
    collect_project_guid_index,
    decode_text_file,
    find_project_root,
    normalize_guid,
    relative_to_root,
    resolve_scope_path,
)
from prefab_sentinel.unity_yaml_parser import split_yaml_blocks

OVERRIDE_TARGET_PATTERN = re.compile(
    r"target:\s*\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([0-9a-fA-F]{32}))?(?:,\s*type:\s*(-?\d+))?\}"
)
ARRAY_SIZE_PATH_PATTERN = re.compile(r"^(?P<prefix>.+)\.Array\.size$")
ARRAY_DATA_PATH_PATTERN = re.compile(r"^(?P<prefix>.+)\.Array\.data\[(?P<index>\d+)\]$")

# Specific code when only one category is present;
# PVR001 is also the fallback for mixed diagnostics.
_STALE_CATEGORY_CODES: dict[frozenset[str], str] = {
    frozenset({"empty_property_path"}): "PVR001",
    frozenset({"duplicate_override"}): "PVR002",
    frozenset({"array_size_mismatch"}): "PVR003",
}


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


@dataclass(slots=True)
class _ChainLevel:
    """One level in the Variant chain walk."""

    entries: list[OverrideEntry]
    path: Path
    depth: int
    is_base: bool
    text: str


def _effective_value(entry: OverrideEntry) -> str:
    """Return the effective value of an override entry.

    Unity stores object references in objectReference; when it is empty or
    ``{fileID: 0}`` the plain ``value`` field is the effective value instead.
    """
    ref = entry.object_reference
    return ref if ref and ref != "{fileID: 0}" else entry.value


class PrefabVariantService:
    """Read-only prefab variant analysis service."""

    TOOL_NAME = "prefab-variant"

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = find_project_root(project_root or Path.cwd())
        self._guid_index: dict[str, Path] | None = None

    def _guid_map(self) -> dict[str, Path]:
        if self._guid_index is None:
            self._guid_index = collect_project_guid_index(self.project_root)
        return self._guid_index

    def _relative(self, path: Path) -> str:
        return relative_to_root(path, self.project_root)

    def _load_variant(self, variant_path: str) -> tuple[Path | None, str | None, ToolResponse | None]:
        path = resolve_scope_path(variant_path, self.project_root)
        if not path.exists():
            return (
                None,
                None,
                error_response(
                    "PVR404",
                    "Variant path does not exist.",
                    data={"variant_path": variant_path, "read_only": True},
                ),
            )
        try:
            text = decode_text_file(path)
        except UnicodeDecodeError:
            return (
                None,
                None,
                error_response(
                    "PVR400",
                    "Variant file could not be decoded as UTF-8/CP932.",
                    data={"variant_path": variant_path, "read_only": True},
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
        """Walk the m_SourcePrefab chain from a Variant up to its base Prefab.

        Args:
            variant_path: Path to a ``.prefab`` Variant asset.

        Returns:
            ``ToolResponse`` with ``data.chain`` listing each Prefab in
            the inheritance chain from the variant to the root base.
        """
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        if path is None or text is None:
            return error_response(
                "PVR_INTERNAL",
                "Internal error: load succeeded but path/text is None.",
            )

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

        return success_response(
            code, message,
            severity=severity,
            data={"variant_path": self._relative(path), "chain": chain, "read_only": True},
            diagnostics=diagnostics,
        )

    def list_overrides(
        self,
        variant_path: str,
        component_filter: str | None = None,
    ) -> ToolResponse:
        """Extract m_Modifications override entries from a Variant.

        Args:
            variant_path: Path to a ``.prefab`` Variant asset.
            component_filter: Optional substring to filter by target or property path.

        Returns:
            ``ToolResponse`` with ``data.overrides`` listing each override's
            target_file_id, property_path, value, and object_reference.
        """
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        if path is None or text is None:
            return error_response(
                "PVR_INTERNAL",
                "Internal error: load succeeded but path/text is None.",
            )

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
        return success_response(
            "PVR_OVERRIDES_OK",
            "Override list extracted.",
            data={
                "variant_path": self._relative(path),
                "component_filter": component_filter,
                "override_count": len(filtered),
                "overrides": payload,
                "read_only": True,
            },
        )

    def compute_effective_values(
        self,
        variant_path: str,
        component_filter: str | None = None,
    ) -> ToolResponse:
        """Compute effective override values using last-write-wins semantics.

        Args:
            variant_path: Path to a ``.prefab`` Variant asset.
            component_filter: Optional substring to filter by target or property path.

        Returns:
            ``ToolResponse`` with ``data.effective_values`` listing the
            winning value for each unique target_key + property_path pair.
        """
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        if path is None or text is None:
            return error_response(
                "PVR_INTERNAL",
                "Internal error: load succeeded but path/text is None.",
            )

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

        return success_response(
            "PVR_EFFECTIVE_OK",
            "Effective override values computed by last-write-wins rule.",
            data={
                "variant_path": self._relative(path),
                "component_filter": component_filter,
                "value_count": len(effective),
                "effective_values": list(effective.values()),
                "read_only": True,
            },
        )

    def detect_stale_overrides(self, variant_path: str) -> ToolResponse:
        """Detect duplicate overrides and array size mismatches in a Variant.

        Args:
            variant_path: Path to a ``.prefab`` Variant asset.

        Returns:
            ``ToolResponse`` with diagnostics for duplicate_override,
            empty_property_path, and array_size_mismatch issues.
        """
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        if path is None or text is None:
            return error_response(
                "PVR_INTERNAL",
                "Internal error: load succeeded but path/text is None.",
            )

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
                        location=f"{lines[0]}:1..{lines[-1]}:1",
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
            categories = {d.detail for d in diagnostics}
            code = _STALE_CATEGORY_CODES.get(frozenset(categories), "PVR001")
            return error_response(
                code,
                "Potential stale overrides detected.",
                severity=Severity.WARNING,
                data={
                    "variant_path": self._relative(path),
                    "stale_count": len(diagnostics),
                    "categories": sorted(categories),
                    "read_only": True,
                },
                diagnostics=diagnostics,
            )

        return success_response(
            "PVR_STALE_NONE",
            "No stale override patterns detected.",
            data={"variant_path": self._relative(path), "stale_count": 0, "read_only": True},
        )

    # ------------------------------------------------------------------
    # Chain-aware before-value resolution — shared traversal helpers
    # ------------------------------------------------------------------

    def _walk_chain_levels(
        self,
        initial_text: str,
        initial_path: Path,
    ) -> Iterator[_ChainLevel]:
        """Yield :class:`_ChainLevel` for each level from variant to base.

        The caller is responsible for initial path validation and decoding.
        This generator handles the chain traversal loop, cycle detection,
        and GUID resolution.  The final level (base prefab) has ``is_base=True``.
        """
        visited: set[str] = set()
        current_text: str | None = initial_text
        current_path: Path = initial_path
        depth_limit = 12

        for depth in range(depth_limit):
            if current_text is None:
                break

            entries = self._parse_overrides(current_text)

            source = SOURCE_PREFAB_PATTERN.search(current_text)
            is_base = source is None

            yield _ChainLevel(
                entries=entries,
                path=current_path,
                depth=depth,
                is_base=is_base,
                text=current_text,
            )

            if is_base:
                break

            assert source is not None  # guaranteed: is_base is False
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

            current_path = target_file

    @staticmethod
    def _iter_base_property_values(
        base_text: str,
    ) -> Iterator[tuple[str, str, str]]:
        """Yield ``(file_id, property_path, value)`` from a base prefab.

        Best-effort extraction of property values from YAML component blocks.
        Array elements written as ``m_Foo`` list items are mapped to
        ``m_Foo.Array.data[N]`` paths.
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
                    yield (file_id, prop_path, item_value)
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

                    yield (file_id, field_name, value_raw)

    # ------------------------------------------------------------------
    # Chain-aware before-value resolution — public API
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
            return {}

        result: dict[str, str] = {}
        for level in self._walk_chain_levels(text, path):
            for entry in level.entries:
                if not entry.property_path:
                    continue
                key = f"{entry.target_file_id}:{entry.property_path}"
                if key not in result:
                    result[key] = _effective_value(entry)

            if level.is_base:
                for fid, pp, val in self._iter_base_property_values(level.text):
                    key = f"{fid}:{pp}"
                    if key not in result:
                        result[key] = val

        return result

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
            return error_response(
                code="PVR404",
                message="Variant path does not exist.",
                data={"variant_path": variant_path, "read_only": True},
            )

        try:
            text = decode_text_file(path)
        except (OSError, UnicodeDecodeError):
            return error_response(
                "PVR_READ_ERROR",
                f"Failed to read variant file: {variant_path}",
                data={"variant_path": variant_path, "read_only": True},
            )

        if SOURCE_PREFAB_PATTERN.search(text) is None:
            return success_response(
                "PVR_NOT_VARIANT",
                "File is not a Variant; no chain to resolve.",
                data={
                    "variant_path": variant_path,
                    "chain": [],
                    "value_count": 0,
                    "values": [],
                    "read_only": True,
                },
            )

        result: dict[str, ChainValue] = {}
        chain: list[dict[str, object]] = []

        for level in self._walk_chain_levels(text, path):
            rel = self._relative(level.path)
            chain.append({"path": rel, "depth": level.depth})

            for entry in level.entries:
                if not entry.property_path:
                    continue
                key = f"{entry.target_file_id}:{entry.property_path}"
                if key not in result:
                    result[key] = ChainValue(
                        target_file_id=entry.target_file_id,
                        property_path=entry.property_path,
                        value=_effective_value(entry),
                        origin_path=rel,
                        origin_depth=level.depth,
                    )

            if level.is_base:
                for fid, pp, val in self._iter_base_property_values(level.text):
                    key = f"{fid}:{pp}"
                    if key not in result:
                        result[key] = ChainValue(
                            target_file_id=fid,
                            property_path=pp,
                            value=val,
                            origin_path=rel,
                            origin_depth=level.depth,
                        )

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

        return success_response(
            "PVR_CHAIN_VALUES_WITH_ORIGIN",
            f"Resolved {len(values_list)} values across {len(chain)} chain levels.",
            data={
                "variant_path": variant_path,
                "chain": chain,
                "value_count": len(values_list),
                "values": values_list,
                "read_only": True,
            },
        )
