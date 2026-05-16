from __future__ import annotations

import unittest

from prefab_sentinel.fuzzy_match import suggest_similar


class TestSuggestSimilar(unittest.TestCase):
    def test_typo_returns_correct_candidate(self) -> None:
        result = suggest_similar("MeshRendrer", ["MeshRenderer", "MeshFilter", "AudioSource"])
        self.assertEqual(
            "MeshRenderer",
            result[0],
            msg=(
                "A 1-edit typo of 'MeshRenderer' must rank that exact "
                "candidate first; observed result="
                f"{result}."
            ),
        )

    def test_complete_mismatch_returns_empty(self) -> None:
        result = suggest_similar("ZZZZZZZZ", ["MeshRenderer", "MeshFilter", "AudioSource"])
        self.assertEqual(
            [],
            result,
            msg=(
                "Inputs whose ratio is below the 0.6 cutoff against "
                "every candidate must produce an empty list, not a "
                "best-effort guess; observed result="
                f"{result}."
            ),
        )

    def test_empty_candidates_returns_empty(self) -> None:
        result = suggest_similar("anything", [])
        self.assertEqual(
            [],
            result,
            msg="An empty candidate iterable must yield an empty result.",
        )

    def test_max_three_results(self) -> None:
        candidates = [f"item_{i}" for i in range(100)]
        result = suggest_similar("item_0", candidates)
        # Upper-bound contract from the function docstring: at most n=3
        # results even when many candidates exceed the cutoff. Using a
        # tuple value-pin so the documented bound and the actual count
        # both surface in the diagnostic on a regression.
        self.assertLessEqual(
            len(result),
            3,
            msg=(
                "suggest_similar must cap results at n=3 even when "
                f"many candidates exceed the cutoff; len(result)={len(result)} "
                f"result={result}."
            ),
        )

    def test_case_sensitive_matching(self) -> None:
        result_default = suggest_similar("_color", ["_Color", "_MainTex"])
        self.assertIn(
            "_Color",
            result_default,
            msg=(
                "Default case-sensitive matching must still admit "
                "candidates that differ only in casing when the ratio "
                "exceeds the cutoff (the underlying difflib ratio is "
                "case-sensitive but high enough here); observed "
                f"result={result_default}."
            ),
        )

    def test_single_char_typo(self) -> None:
        result = suggest_similar("_Colr", ["_Color", "_MainTex", "_BumpMap"])
        self.assertIn(
            "_Color",
            result,
            msg=(
                "A 1-character omission ('_Colr' vs '_Color') must "
                "leave '_Color' in the suggestions; observed "
                f"result={result}."
            ),
        )
