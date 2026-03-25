from __future__ import annotations

import unittest

from prefab_sentinel.fuzzy_match import suggest_similar


class TestSuggestSimilar(unittest.TestCase):
    def test_typo_returns_correct_candidate(self) -> None:
        result = suggest_similar("MeshRendrer", ["MeshRenderer", "MeshFilter", "AudioSource"])
        self.assertEqual(result[0], "MeshRenderer")

    def test_complete_mismatch_returns_empty(self) -> None:
        result = suggest_similar("ZZZZZZZZ", ["MeshRenderer", "MeshFilter", "AudioSource"])
        self.assertEqual(result, [])

    def test_empty_candidates_returns_empty(self) -> None:
        result = suggest_similar("anything", [])
        self.assertEqual(result, [])

    def test_max_three_results(self) -> None:
        candidates = [f"item_{i}" for i in range(100)]
        result = suggest_similar("item_0", candidates)
        self.assertLessEqual(len(result), 3)

    def test_case_sensitive_matching(self) -> None:
        result_default = suggest_similar("_color", ["_Color", "_MainTex"])
        self.assertIn("_Color", result_default)

    def test_single_char_typo(self) -> None:
        result = suggest_similar("_Colr", ["_Color", "_MainTex", "_BumpMap"])
        self.assertIn("_Color", result)
