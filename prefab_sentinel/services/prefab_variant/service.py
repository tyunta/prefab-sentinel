"""Read-only Prefab Variant analysis service.

Orchestrates the override parser, stale-override detector, and chain
walker to provide high-level Variant inspection methods.  All write
concerns live in ``serialized_object`` and ``orchestrator_patch``.
"""

from __future__ import annotations

from pathlib import Path

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response, success_response
from prefab_sentinel.services.prefab_variant.chain import walk_chain_levels
from prefab_sentinel.services.prefab_variant.chain_values import (
    resolve_chain_values as _resolve_chain_values,
    resolve_chain_values_with_origin as _resolve_chain_values_with_origin,
)
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry, parse_overrides
from prefab_sentinel.services.prefab_variant.stale import detect_stale
from prefab_sentinel.unity_assets import (
    collect_project_guid_index,
    decode_text_file,
    find_project_root,
)
from prefab_sentinel.unity_assets_path import relative_to_root, resolve_scope_path


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
                    "Variant file could not be decoded as UTF-8.",
                    data={"variant_path": variant_path, "read_only": True},
                ),
            )
        return path, text, None

    def _walk_chain_levels(
        self,
        initial_text: str,
        initial_path: Path,
        diagnostics: list[Diagnostic],
    ):
        """Walk the variant chain, appending diagnostics for decode/resolve failures.

        Thin wrapper around :func:`walk_chain_levels` that injects the
        service's GUID map and relative-path helper so the free function
        stays decoupled from the service instance.
        """
        return walk_chain_levels(
            initial_text,
            initial_path,
            self._guid_map(),
            self._relative,
            diagnostics,
        )

    def resolve_prefab_chain(self, variant_path: str) -> ToolResponse:
        """Walk the m_SourcePrefab chain from a Variant up to its base Prefab.

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

        diagnostics: list[Diagnostic] = []
        chain: list[dict[str, object]] = []
        for level in self._walk_chain_levels(text, path, diagnostics):
            chain.append({"path": self._relative(level.path), "guid": None})

        # When the walk aborted on missing_asset, append an entry for the
        # unresolved guid so the chain list still shows what was attempted.
        for diag in diagnostics:
            if diag.detail == "missing_asset":
                chain.append({"path": "", "guid": diag.evidence})

        if diagnostics:
            severity = Severity.WARNING
            code = "PVR_CHAIN_WARN"
            message = "Prefab chain resolved with warnings."
        else:
            severity = Severity.INFO
            code = "PVR_CHAIN_OK"
            message = "Prefab chain resolved."

        return success_response(
            code,
            message,
            severity=severity,
            data={"variant_path": self._relative(path), "chain": chain, "read_only": True},
            diagnostics=diagnostics,
        )

    def list_overrides(
        self,
        variant_path: str,
        component_filter: str | None = None,
    ) -> ToolResponse:
        """Extract m_Modifications override entries from a Variant."""
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        if path is None or text is None:
            return error_response(
                "PVR_INTERNAL",
                "Internal error: load succeeded but path/text is None.",
            )

        entries = parse_overrides(text)
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
        """Compute effective override values using last-write-wins semantics."""
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        if path is None or text is None:
            return error_response(
                "PVR_INTERNAL",
                "Internal error: load succeeded but path/text is None.",
            )

        entries = parse_overrides(text)
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
        """Detect duplicate overrides and array size mismatches in a Variant."""
        path, text, error = self._load_variant(variant_path)
        if error is not None:
            return error
        if path is None or text is None:
            return error_response(
                "PVR_INTERNAL",
                "Internal error: load succeeded but path/text is None.",
            )

        entries = parse_overrides(text)
        return detect_stale(entries, path, self._relative)

    def resolve_chain_values(
        self,
        variant_path: str,
        diagnostics: list[Diagnostic] | None = None,
    ) -> dict[str, str]:
        """Walk the full Variant chain and return effective override values.

        When ``diagnostics`` is supplied, an initial-variant decode
        failure and any walk-level diagnostics are appended to the sink;
        otherwise the historical silent-swallow contract is preserved.
        """
        return _resolve_chain_values(
            variant_path,
            self.project_root,
            resolve_scope_path,
            self._guid_map(),
            self._relative,
            diagnostics,
        )

    def resolve_chain_values_with_origin(self, variant_path: str) -> ToolResponse:
        """Walk the full Variant chain and return values with origin annotations."""
        return _resolve_chain_values_with_origin(
            variant_path,
            self.project_root,
            resolve_scope_path,
            self._guid_map(),
            self._relative,
        )


__all__ = ["PrefabVariantService", "OverrideEntry"]
