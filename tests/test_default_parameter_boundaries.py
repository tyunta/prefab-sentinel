"""±1 boundary triplets for numeric default parameter values acting as
caps in the audited package (issue #179).

Audit method (re-runnable by a future surveyor):

    grep -rnE "def .* = [0-9]+" prefab_sentinel/

Filter rule applied to the result set: keep defaults that act as
truncation caps, size limits, or thresholds; exclude defaults that
function as flags or sentinel counts.

Discovered set for the current ``prefab_sentinel/`` tree (exactly two
sites):

* ``prefab_sentinel.services.runtime_validation.classification.classify_errors``
  — ``max_diagnostics: int = 200`` (truncation cap on the diagnostics
  list).
* ``prefab_sentinel.services.reference_resolver.ReferenceResolverService.scan_broken_references``
  — ``top_guid_limit: int = 10`` (size limit on the
  ``top_missing_asset_guids`` report).

Per-site triplets:

* Classification cap: for 199 / 200 / 201 input lines the diagnostics
  length is capped at 200 and ``count_total`` reflects every hit.
* Top-GUID limit: for 9 / 10 / 11 distinct missing GUIDs the top list
  length is capped at 10.

Boundary triplets fire ``classify_errors`` and ``scan_broken_references``
**without an explicit override**, so a mutation that flips the default
literal (e.g. 200 to 201, or 10 to 9) breaks the equality assertion.

If a future audit surfaces additional default-parameter cap sites, they
fall under issue #179's follow-up scope rather than this batch.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.services.runtime_validation import RuntimeValidationService

_DEFAULT_CLASSIFICATION_CAP = 200
_DEFAULT_TOP_GUID_LIMIT = 10


class ClassificationDiagnosticCapBoundaryTests(unittest.TestCase):
    """Triplet for ``classify_errors``'s ``max_diagnostics: int = 200``."""

    def _classify(self, line_count: int):
        svc = RuntimeValidationService()
        log_lines = [f"Broken PPtr in file_{n}" for n in range(line_count)]
        # Intentionally invoked without an explicit ``max_diagnostics``
        # so the default literal participates in the boundary check.
        return svc.classify_errors(log_lines)

    def test_input_one_below_cap_does_not_truncate(self) -> None:
        below = _DEFAULT_CLASSIFICATION_CAP - 1
        resp = self._classify(below)
        self.assertEqual(below, resp.data["count_total"])
        self.assertEqual(below, len(resp.diagnostics))
        self.assertEqual(below, resp.data["returned_diagnostics"])
        self.assertEqual(0, resp.data["truncated_diagnostics"])

    def test_input_at_cap_returns_exactly_cap_diagnostics(self) -> None:
        at = _DEFAULT_CLASSIFICATION_CAP
        resp = self._classify(at)
        self.assertEqual(at, resp.data["count_total"])
        self.assertEqual(at, len(resp.diagnostics))
        self.assertEqual(at, resp.data["returned_diagnostics"])
        self.assertEqual(0, resp.data["truncated_diagnostics"])

    def test_input_one_above_cap_truncates_diagnostics(self) -> None:
        above = _DEFAULT_CLASSIFICATION_CAP + 1
        resp = self._classify(above)
        self.assertEqual(above, resp.data["count_total"])
        self.assertEqual(_DEFAULT_CLASSIFICATION_CAP, len(resp.diagnostics))
        self.assertEqual(
            _DEFAULT_CLASSIFICATION_CAP,
            resp.data["returned_diagnostics"],
        )
        self.assertEqual(1, resp.data["truncated_diagnostics"])


class TopMissingGuidLimitBoundaryTests(unittest.TestCase):
    """Triplet for ``scan_broken_references``'s ``top_guid_limit: int = 10``.

    Each missing GUID appears once in a single source asset; the scan
    reports them via ``top_missing_asset_guids`` ranked by occurrence.
    """

    def _build_project(self, root: Path, missing_guid_count: int) -> None:
        (root / "Assets").mkdir(parents=True, exist_ok=True)
        # Seed one source asset that references ``missing_guid_count``
        # distinct GUIDs that have no corresponding ``.meta`` file.
        body_lines = ["%YAML 1.1", "--- !u!114 &11400000", "MonoBehaviour:"]
        for index in range(missing_guid_count):
            guid = f"{index:032x}"
            body_lines.append(
                f"  m_Ref{index}: {{fileID: 11400000, guid: {guid}, type: 2}}"
            )
        (root / "Assets" / "Source.asset").write_text(
            "\n".join(body_lines) + "\n", encoding="utf-8"
        )
        (root / "Assets" / "Source.asset.meta").write_text(
            "fileFormatVersion: 2\nguid: 9999999999999999999999999999aaaa\n",
            encoding="utf-8",
        )

    def _scan(self, missing_guid_count: int):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._build_project(root, missing_guid_count)
            svc = ReferenceResolverService(project_root=root)
            # Intentionally invoked without an explicit
            # ``top_guid_limit`` so the default literal participates.
            return svc.scan_broken_references("Assets")

    def test_below_limit_emits_all_distinct_guids(self) -> None:
        below = _DEFAULT_TOP_GUID_LIMIT - 1
        response = self._scan(below)
        self.assertEqual(below, len(response.data["top_missing_asset_guids"]))

    def test_at_limit_emits_exactly_limit_guids(self) -> None:
        at = _DEFAULT_TOP_GUID_LIMIT
        response = self._scan(at)
        self.assertEqual(at, len(response.data["top_missing_asset_guids"]))

    def test_above_limit_truncates_to_limit(self) -> None:
        above = _DEFAULT_TOP_GUID_LIMIT + 1
        response = self._scan(above)
        self.assertEqual(
            _DEFAULT_TOP_GUID_LIMIT,
            len(response.data["top_missing_asset_guids"]),
        )


if __name__ == "__main__":
    unittest.main()
