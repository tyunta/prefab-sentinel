"""Fuzzy matching utilities for error hint suggestions."""

from __future__ import annotations

from collections.abc import Iterable
from difflib import get_close_matches


def suggest_similar(
    word: str,
    candidates: Iterable[str],
    *,
    n: int = 3,
    cutoff: float = 0.6,
) -> list[str]:
    """Return up to *n* candidates similar to *word*.

    Uses ``difflib.SequenceMatcher`` (ratio >= *cutoff*).
    Returns an empty list when no candidate exceeds the threshold.
    """
    return get_close_matches(word, list(candidates), n=n, cutoff=cutoff)
