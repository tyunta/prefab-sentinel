"""Structured ``SER003`` envelope helpers for unresolved property/component lookups.

When ``set_component_fields`` (or any caller routed through the dry-run
path) cannot resolve a referenced component type or property path on the
chain, the response should be a structured fail-fast error instead of a
soft warning. These helpers build that envelope, attaching did-you-mean
suggestions ranked by ``prefab_sentinel.fuzzy_match.suggest_similar``.
"""

from __future__ import annotations

from collections.abc import Iterable

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    error_response,
)
from prefab_sentinel.fuzzy_match import suggest_similar

# Audit code shared across both not-found classifications. The two cases
# are distinguished via ``Diagnostic.detail``: ``property_not_found`` vs
# ``component_not_found``.
SER003 = "SER003"


def _ranked_unique(candidates: Iterable[str]) -> list[str]:
    """Return *candidates* deduplicated, preserving the first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# Maximum number of did-you-mean suggestions to surface when fuzzy
# matching produces no close hit. Bounds the response payload while
# still giving the caller something actionable to grep.
_MAX_FALLBACK_SUGGESTIONS = 5


def _suggest_or_fallback(needle: str, candidates: list[str]) -> list[str]:
    """Fuzzy-match *needle* against *candidates*; fall back to a head slice.

    When the fuzzy matcher rejects every candidate (typical for very short
    property names that share little overlap with the typo), return the
    first ``_MAX_FALLBACK_SUGGESTIONS`` candidate strings instead so the
    caller still receives an actionable list.
    """
    matches = suggest_similar(needle, candidates)
    if matches:
        return matches
    return candidates[:_MAX_FALLBACK_SUGGESTIONS]


def resolve_property_not_found(
    target: str,
    component: str,
    property_path: str,
    candidate_property_paths: Iterable[str],
) -> ToolResponse:
    """Return a ``SER003`` envelope for an unresolved property path.

    The diagnostic is tagged with ``detail="property_not_found"``;
    ``data.suggestions`` carries the closest matches drawn from
    *candidate_property_paths* using the project's fuzzy matcher.
    """
    candidates = _ranked_unique(candidate_property_paths)
    suggestions = _suggest_or_fallback(property_path, candidates)
    diagnostic = Diagnostic(
        path=target,
        location=f"component '{component}' property '{property_path}'",
        detail="property_not_found",
        evidence=(
            f"property path '{property_path}' did not resolve on component "
            f"'{component}' for target '{target}'"
        ),
    )
    return error_response(
        SER003,
        f"Property '{property_path}' not found on component '{component}'",
        severity=Severity.ERROR,
        data={
            "target": target,
            "component": component,
            "property_path": property_path,
            "suggestions": suggestions,
            "read_only": True,
        },
        diagnostics=[diagnostic],
    )


def resolve_component_not_found(
    target: str,
    component: str,
    candidate_component_types: Iterable[str],
) -> ToolResponse:
    """Return a ``SER003`` envelope for an unresolved component type name.

    The diagnostic is tagged with ``detail="component_not_found"``;
    ``data.suggestions`` carries the closest matches drawn from
    *candidate_component_types* using the project's fuzzy matcher.
    """
    candidates = _ranked_unique(candidate_component_types)
    suggestions = _suggest_or_fallback(component, candidates)
    diagnostic = Diagnostic(
        path=target,
        location=f"component '{component}'",
        detail="component_not_found",
        evidence=(
            f"component type '{component}' is not present in the chain for "
            f"target '{target}'"
        ),
    )
    return error_response(
        SER003,
        f"Component type '{component}' not found in chain",
        severity=Severity.ERROR,
        data={
            "target": target,
            "component": component,
            "suggestions": suggestions,
            "read_only": True,
        },
        diagnostics=[diagnostic],
    )


__all__ = [
    "SER003",
    "resolve_component_not_found",
    "resolve_property_not_found",
]
