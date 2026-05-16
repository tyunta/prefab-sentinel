"""Variant inspection functions extracted from Phase1Orchestrator.

Also provides ``_read_target_file`` and ``_resolve_variant_base`` helpers
shared by orchestrator_wiring, orchestrator_inspect, and
orchestrator_validation.
"""

from __future__ import annotations

from typing import Any

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
    max_severity,
    success_response,
)
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.unity_assets import (
    decode_text_file,
    is_variant_prefab,
)
from prefab_sentinel.unity_assets_path import resolve_scope_path

# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


def _read_target_file(
    prefab_variant: PrefabVariantService,
    target_path: str,
    code_prefix: str,
) -> ToolResponse | str:
    """Read a Unity YAML file, returning text on success or an error ToolResponse."""
    path = resolve_scope_path(target_path, prefab_variant.project_root)
    if not path.exists():
        return error_response(
            f"{code_prefix}_FILE_NOT_FOUND",
            f"Target file does not exist: {target_path}",
            data={"target_path": target_path, "read_only": True},
        )
    try:
        return decode_text_file(path)
    except (OSError, UnicodeDecodeError) as exc:
        return error_response(
            f"{code_prefix}_READ_ERROR",
            f"Failed to read target file: {exc}",
            data={"target_path": target_path, "read_only": True},
        )


def _resolve_variant_base(
    prefab_variant: PrefabVariantService,
    text: str,
    target_path: str,
    code_prefix: str,
) -> tuple[str, bool, str | None, list[Diagnostic]]:
    """If *text* is a Variant, resolve the chain and return the base text.

    Returns ``(text, is_variant, base_prefab_path, chain_diagnostics)``.
    """
    if not is_variant_prefab(text):
        return text, False, None, []

    chain_response = prefab_variant.resolve_prefab_chain(target_path)
    chain = chain_response.data.get("chain", [])
    chain_diagnostics = list(chain_response.diagnostics)

    base_path: str | None = None
    for entry in reversed(chain):
        entry_path = entry.get("path", "")
        if entry_path and entry_path != target_path:
            base_path = entry_path
            break

    if base_path:
        base_text_or_error = _read_target_file(prefab_variant, base_path, code_prefix)
        if not isinstance(base_text_or_error, ToolResponse):
            return base_text_or_error, True, base_path, chain_diagnostics

    return text, False, None, chain_diagnostics


# ------------------------------------------------------------------
# inspect_variant / diff_variant
# ------------------------------------------------------------------


def inspect_variant(
    prefab_variant: PrefabVariantService,
    variant_path: str,
    component_filter: str | None = None,
    *,
    show_origin: bool = False,
) -> ToolResponse:
    named_steps: list[tuple[str, ToolResponse]] = [
        ("resolve_prefab_chain", prefab_variant.resolve_prefab_chain(variant_path)),
        ("list_overrides", prefab_variant.list_overrides(variant_path, component_filter)),
        (
            "compute_effective_values",
            prefab_variant.compute_effective_values(variant_path, component_filter),
        ),
        ("detect_stale_overrides", prefab_variant.detect_stale_overrides(variant_path)),
    ]
    if show_origin:
        named_steps.append((
            "resolve_chain_values_with_origin",
            prefab_variant.resolve_chain_values_with_origin(variant_path),
        ))
    executed_steps: list[dict[str, object]] = []
    diagnostics: list[Diagnostic] = []
    severities: list[Severity] = []
    fail_fast = False
    for step_name, step in named_steps:
        executed_steps.append({"step": step_name, "result": step.to_dict()})
        diagnostics.extend(step.diagnostics)
        severities.append(step.severity)
        if step.severity in (Severity.ERROR, Severity.CRITICAL):
            fail_fast = True
            break

    severity = max_severity(severities)
    success = severity not in (Severity.ERROR, Severity.CRITICAL)
    return ToolResponse(
        success=success,
        severity=severity,
        code="INSPECT_VARIANT_RESULT",
        message=(
            "inspect.variant pipeline completed (read-only)."
            if not fail_fast
            else "inspect.variant stopped by fail-fast policy due to error severity."
        ),
        data={
            "variant_path": variant_path,
            "component_filter": component_filter,
            "read_only": True,
            "fail_fast_triggered": fail_fast,
            "steps": executed_steps,
        },
        diagnostics=diagnostics,
    )


def diff_variant(
    prefab_variant: PrefabVariantService,
    variant_path: str,
    component_filter: str | None = None,
) -> ToolResponse:
    chain_resp = prefab_variant.resolve_chain_values_with_origin(variant_path)
    if not chain_resp.success:
        return chain_resp

    values = chain_resp.data.get("values", [])
    chain = chain_resp.data.get("chain", [])

    by_key: dict[str, list[dict[str, Any]]] = {}
    for v in values:
        key = f"{v['target_file_id']}:{v['property_path']}"
        by_key.setdefault(key, []).append(v)

    diffs: list[dict[str, Any]] = []
    for _key, entries in by_key.items():
        variant_entry = None
        base_entry = None
        for e in entries:
            if e["origin_depth"] == 0:
                variant_entry = e
            elif base_entry is None or e["origin_depth"] < base_entry["origin_depth"]:
                base_entry = e
        if variant_entry is None:
            continue
        diff: dict[str, Any] = {
            "target_file_id": variant_entry["target_file_id"],
            "property_path": variant_entry["property_path"],
            "variant_value": variant_entry["value"],
            "base_value": base_entry["value"] if base_entry else None,
            "base_origin_path": base_entry["origin_path"] if base_entry else None,
            "base_origin_depth": base_entry["origin_depth"] if base_entry else None,
        }
        diffs.append(diff)

    if component_filter:
        needle = component_filter.lower()
        diffs = [d for d in diffs if needle in d["property_path"].lower()]

    return success_response(
        "DIFF_VARIANT_OK",
        f"Found {len(diffs)} differences.",
        data={
            "variant_path": variant_path,
            "component_filter": component_filter,
            "diff_count": len(diffs),
            "diffs": diffs,
            "chain": chain,
            "read_only": True,
        },
    )
