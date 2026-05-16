"""Syntactic validation of Unity ``propertyPath`` strings.

Unity SerializedProperty paths are dot-separated segments with optional
``[N]`` subscripts on ``Array.data`` segments::

    m_Transform.m_LocalPosition.x
    m_List.Array.data[3]
    m_Outer.Array.data[0].m_Inner.Array.data[1]

This module validates the **syntax only** — it does not resolve the path
against any concrete target.  Semantic errors (path exists, index in
bounds, etc.) are the caller's concern.

Error code contract (issue #82):

- ``SER001`` — shape errors: empty path, empty segment, unterminated
  ``[`` bracket.
- ``SER002`` — index errors: negative index (Unity does not interpret
  ``[-1]`` as last-element like Python), non-integer index, or
  ``Array.size`` used with a ``[N]`` subscript (``Array.size`` is scalar
  and cannot be indexed).

Success returns ``PP_OK`` at ``severity=info``.
"""

from __future__ import annotations

import re

from prefab_sentinel.contracts import Severity, ToolResponse, error_response, success_response

# Matches a trailing ``[N]`` subscript on a segment.  Captures the
# inner text so we can validate it (non-greedy so the leading text
# is maximized).
_SUBSCRIPT_PATTERN = re.compile(r"^(.*?)\[([^\]]*)\]$")


def _make_error(code: str, message: str, path: str) -> ToolResponse:
    return error_response(
        code,
        message,
        severity=Severity.ERROR,
        data={"property_path": path},
    )


def validate_property_path(path: str) -> ToolResponse:
    """Validate the syntax of a Unity ``propertyPath`` string.

    Returns ``PP_OK`` (info) on success, ``SER001`` (error) for shape
    problems, and ``SER002`` (error) for index problems.  Never raises —
    even malformed input produces a structured envelope.
    """
    if not path:
        return _make_error("SER001", "propertyPath is empty.", path)

    segments = path.split(".")
    # Track whether the previous segment was ``Array.size`` so we can
    # detect an illegal subscript on the next segment.  ``Array.size``
    # itself is a scalar and must not carry ``[N]``.
    for index, segment in enumerate(segments):
        if not segment:
            return _make_error(
                "SER001",
                "propertyPath contains an empty segment (consecutive dots or trailing dot).",
                path,
            )

        # Reject unterminated brackets as shape errors before further
        # structural checks.
        if "[" in segment and not segment.endswith("]"):
            return _make_error(
                "SER001",
                "propertyPath contains an unterminated '[' bracket.",
                path,
            )

        subscript_match = _SUBSCRIPT_PATTERN.match(segment)
        if subscript_match is None:
            # No subscript on this segment — nothing more to check.
            continue

        head, inner = subscript_match.group(1), subscript_match.group(2)

        # ``Array.size[N]`` is invalid: ``Array.size`` is a scalar.
        # Detect either a segment literally named ``size[N]`` following
        # an ``Array`` segment, or a head of ``size`` with the previous
        # segment being ``Array``.
        if head == "size" and index > 0 and segments[index - 1] == "Array":
            return _make_error(
                "SER002",
                "propertyPath uses Array.size with a subscript; Array.size is scalar and cannot be indexed.",
                path,
            )

        # Inner subscript must be a non-negative integer.
        if not inner:
            return _make_error(
                "SER001",
                "propertyPath subscript is empty ('[]').",
                path,
            )
        try:
            inner_int = int(inner)
        except ValueError:
            return _make_error(
                "SER002",
                f"propertyPath subscript must be an integer; got '[{inner}]'.",
                path,
            )
        if inner_int < 0:
            return _make_error(
                "SER002",
                f"propertyPath subscript has a negative index '[{inner}]'; Unity does not interpret this as last-element.",
                path,
            )

    return success_response(
        "PP_OK",
        "propertyPath syntax is valid.",
        data={"property_path": path},
    )


__all__ = ["validate_property_path"]
