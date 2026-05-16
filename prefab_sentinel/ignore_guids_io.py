"""Loader for the conventional ``<scope>/config/ignore_guids.txt`` file.

The operating convention documented in ``CLAUDE.md`` places the baseline
ignore-GUID list at ``<scope>/config/ignore_guids.txt``.  When the file is
absent the convention treats the scope as having no baseline entries; the
loader returns an empty list in that case so callers can union without
branching.

The loader parses ``#``-introduced line comments and discards blank lines;
GUID-shape validation is left to the service-layer envelope (REF001) so a
single typo surfaces through the existing validation path rather than
being silently dropped at the loader boundary.
"""

from __future__ import annotations

import logging
from pathlib import Path

__all__ = [
    "IGNORE_GUIDS_RELATIVE_PATH",
    "load_ignore_guids_file",
    "merge_ignore_guids",
    "parse_ignore_guid_text",
]

logger = logging.getLogger(__name__)

# CLAUDE.md operating convention: the baseline ignore-GUID list lives at
# ``<scope>/config/ignore_guids.txt`` and the absent-file case is silently
# ignored.  The relative path is exported so the validation MCP tool can
# include the resolved absolute path in the informational diagnostic
# without re-deriving the convention here.
IGNORE_GUIDS_RELATIVE_PATH = Path("config") / "ignore_guids.txt"


def parse_ignore_guid_text(text: str) -> list[str]:
    """Return GUID entries parsed from raw ignore-GUID text (issue #256).

    Pure function over ``str``: blank lines and ``#``-introduced
    comments are dropped, inline trailers are stripped, and the
    remaining entries are returned in document order.  Empty input
    yields an empty list.  GUID-shape validation is the orchestrator's
    REF001 responsibility — the parser is shape-agnostic so a single
    typo surfaces through the existing validation path rather than
    being silently dropped at the parser boundary.

    Args:
        text: Raw text content (typically the result of ``read_text``
            on the ignore-GUID file).  Any string is accepted; no
            exception is raised for malformed-looking content.

    Returns:
        Parsed entries in document order with comments and blank
        lines removed.
    """
    result: list[str] = []
    for raw_line in text.splitlines():
        # Split-then-strip so trailing whitespace before the marker is
        # discarded along with the comment, and the same rule applies
        # to comment-only lines (which collapse to ``""``).
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        result.append(line)
    return result


def load_ignore_guids_file(scope: str | Path) -> list[str]:
    """Return GUID strings parsed from ``<scope>/config/ignore_guids.txt``.

    Blank lines and ``#``-introduced comments are excluded.  Returns an
    empty list when the file is absent, when *scope* is not a directory,
    or when the file cannot be decoded.

    Args:
        scope: Scope directory whose ``config/ignore_guids.txt`` should
            be read.  Accepts ``str`` and ``Path``.

    Returns:
        Parsed GUID strings in document order.  Returns ``[]`` on the
        documented "absent file is silently ignored" path; no exception
        is raised.
    """
    scope_path = Path(scope)
    if not scope_path.is_dir():
        return []

    file_path = scope_path / IGNORE_GUIDS_RELATIVE_PATH
    if not file_path.is_file():
        return []

    try:
        text = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        # Convention: absent and unreadable are identical.  Logging at
        # debug level preserves diagnosability without surfacing a
        # diagnostic to the response (the operating convention is silent
        # in this case).
        logger.debug(
            "Failed to read ignore-GUID file %s: %s",
            file_path,
            exc,
        )
        return []

    # Issue #256: comment / blank-line / order semantics live in the
    # canonical parser so the benchmark CLI adapter and this loader
    # cannot drift.
    return parse_ignore_guid_text(text)


def merge_ignore_guids(
    caller: list[str] | None,
    file_entries: list[str],
) -> tuple[str, ...]:
    """Union *caller* and *file_entries* preserving first-seen order.

    Caller-supplied entries lead so the operator's explicit intent is
    recorded before the baseline file contents.  Duplicates are removed
    via the first-seen position so a GUID present in both sources is
    counted once and keeps its caller-list position.  The result is a
    tuple because the orchestrator's ``ignore_asset_guids`` parameter
    is typed as ``tuple[str, ...]``.

    The MCP ``validate_refs`` tool is the surviving consumer; the helper
    is kept centralised so a future change to the dedup discipline lands
    in one place.
    """
    seen: dict[str, None] = {}
    for guid in caller or ():
        seen.setdefault(guid, None)
    for guid in file_entries:
        seen.setdefault(guid, None)
    return tuple(seen.keys())
