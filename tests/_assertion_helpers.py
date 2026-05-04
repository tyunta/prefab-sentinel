"""Test-side helper for verifying structured response envelopes by value.

Why a helper: the project's response envelope is the public contract surface
captured by ``CLAUDE.md`` (``success / severity / code / message / data /
diagnostics``).  Tests that only assert "an error was raised" allow mutations
that swap codes, change severities, or rename fields to escape detection.
This helper concentrates per-field equality checks so individual tests pin
behaviour by value with one call.

The helper accepts both shapes the codebase uses for failure envelopes:

* ``ToolResponse`` instances (``prefab_sentinel.contracts.ToolResponse``).
* Plain dicts produced by ``error_dict`` / dict-style services.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

__all__ = ["assert_error_envelope"]


def _envelope_get(envelope: object, field: str) -> tuple[bool, Any]:
    """Return ``(present, value)`` for ``field`` on either dict or dataclass."""
    if isinstance(envelope, Mapping):
        if field in envelope:
            return True, envelope[field]
        return False, None
    if hasattr(envelope, field):
        return True, getattr(envelope, field)
    return False, None


def _coerce_severity(value: Any) -> str:
    """Convert ``Severity`` enum members to their string value; pass others through."""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def assert_error_envelope(
    response: object,
    *,
    code: str,
    severity: str = "error",
    field: str | None = None,
    message_match: str | None = None,
) -> None:
    """Verify a failure-shape envelope by value.

    Returns silently on a full match.  Raises ``AssertionError`` whose text
    identifies the first non-matching envelope field with both expected and
    observed values.

    Args:
        response: A ``ToolResponse``-like object or dict carrying the envelope.
        code: Expected ``code`` value (e.g. ``"REF001"``).
        severity: Expected ``severity`` value; defaults to ``"error"``.
        field: When supplied, asserts the failure points at this field name.
            Looked up under ``data.field``, then ``data.fields`` (list/tuple
            membership), then top-level ``field`` on the envelope.
        message_match: When supplied, a regex pattern that must search-match
            the envelope's ``message`` text.
    """
    # success flag must be False on a failure envelope.
    present, success_value = _envelope_get(response, "success")
    if not present:
        raise AssertionError(
            f"envelope missing 'success' field; got {response!r}"
        )
    if bool(success_value) is True:
        raise AssertionError(
            f"envelope 'success' is True; expected a failure response, got {response!r}"
        )

    # code: exact equality.
    _, observed_code = _envelope_get(response, "code")
    if observed_code != code:
        raise AssertionError(
            f"envelope 'code' mismatch: expected {code!r}, observed {observed_code!r}"
        )

    # severity: compared as string after enum coercion.
    _, observed_severity_raw = _envelope_get(response, "severity")
    observed_severity = _coerce_severity(observed_severity_raw)
    if observed_severity != severity:
        raise AssertionError(
            f"envelope 'severity' mismatch: expected {severity!r}, "
            f"observed {observed_severity!r}"
        )

    # field: searched on data.field, data.fields, then top-level.
    if field is not None:
        _, data = _envelope_get(response, "data")
        candidates: list[Any] = []
        if isinstance(data, Mapping):
            if "field" in data:
                candidates.append(data["field"])
            if "fields" in data and isinstance(data["fields"], (list, tuple)):
                candidates.extend(data["fields"])
        _, top_field = _envelope_get(response, "field")
        if top_field is not None:
            candidates.append(top_field)
        if field not in candidates:
            raise AssertionError(
                f"envelope 'field' mismatch: expected {field!r}, "
                f"observed candidates {candidates!r}"
            )

    # message: regex search.
    if message_match is not None:
        _, observed_message = _envelope_get(response, "message")
        observed_text = "" if observed_message is None else str(observed_message)
        if re.search(message_match, observed_text) is None:
            raise AssertionError(
                f"envelope 'message' regex mismatch: pattern {message_match!r} "
                f"did not match {observed_text!r}"
            )
