"""±1 boundary triplets for numeric default parameter values acting as
caps in the audited package (issue #179).

Audit method (re-runnable by a future surveyor):

    grep -rnE "def .* = [0-9]+" prefab_sentinel/

Filter rule applied to the result set: keep defaults that act as
truncation caps, size limits, or thresholds; exclude defaults that
function as flags or sentinel counts.

Discovered set for the current ``prefab_sentinel/`` tree (exactly three
sites):

* ``prefab_sentinel.services.runtime_validation.classification.classify_errors``
  — ``max_diagnostics: int = 200`` (truncation cap on the diagnostics
  list).
* ``prefab_sentinel.services.reference_resolver.ReferenceResolverService.scan_broken_references``
  — ``top_guid_limit: int = 10`` (size limit on the
  ``top_missing_asset_guids`` report).
* ``prefab_sentinel.orchestrator_wiring.inspect_wiring`` —
  ``page_size: int = INSPECT_WIRING_PAGE_SIZE_DEFAULT`` (issue #197;
  page-size cap on the merged components list slice; default literal
  is ``50``, inclusive bounds ``[1, 500]``).

Per-site triplets:

* Classification cap: for 199 / 200 / 201 input lines the diagnostics
  length is capped at 200 and ``count_total`` reflects every hit.
* Top-GUID limit: for 9 / 10 / 11 distinct missing GUIDs the top list
  length is capped at 10.
* Inspect-wiring page size: for 49 / 50 / 51 merged components the
  page slice length matches the total below the default and is capped
  at the default with a continuation token above it.

Boundary triplets fire ``classify_errors``, ``scan_broken_references``,
and ``inspect_wiring`` **without an explicit override**, so a mutation
that flips the default literal (e.g. 200 to 201, 10 to 9, or 50 to 51)
breaks the equality assertion.

If a future audit surfaces additional default-parameter cap sites, they
fall under issue #179's follow-up scope rather than this batch.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.orchestrator_wiring import (
    INSPECT_WIRING_CURSOR_PREFIX,
    INSPECT_WIRING_PAGE_SIZE_DEFAULT,
    inspect_wiring,
)
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.services.runtime_validation import RuntimeValidationService
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_monobehaviour,
    make_prefab_instance,
    make_transform,
)

_DEFAULT_CLASSIFICATION_CAP = 200
_DEFAULT_TOP_GUID_LIMIT = 10
# Issue #197 anchor; mirrored by INSPECT_WIRING_PAGE_SIZE_DEFAULT.
_DEFAULT_INSPECT_WIRING_PAGE_SIZE = 50

_BOUNDARY_BASE_GUID = "11111111111111111111111111111111"
_BOUNDARY_CHILD_GUID = "22222222222222222222222222222222"
_BOUNDARY_SCRIPT_GUID = "33333333333333333333333333333333"


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


class InspectWiringPageSizeBoundaryTests(unittest.TestCase):
    """Triplet for ``inspect_wiring``'s ``page_size: int = 50`` (issue #197).

    Constructs a project with exactly N merged components (1 root +
    N-1 nested), then calls ``inspect_wiring`` *without* an explicit
    ``page_size`` so the default literal participates. A mutation that
    flips the default literal (e.g. 50 → 51 or 50 → 49) flips one of
    the three triplet outcomes.
    """

    def _build(self, root: Path, total: int) -> Path:
        assets = root / "Assets"
        assets.mkdir(parents=True, exist_ok=True)
        nested_count = total - 1
        child_component_fids = [str(200 + i) for i in range(nested_count)]
        child_text = (
            YAML_HEADER
            + make_gameobject("100", "ChildObj", ["110"] + child_component_fids)
            + make_transform("110", "100")
        )
        for fid in child_component_fids:
            child_text += make_monobehaviour(fid, "100", guid=_BOUNDARY_SCRIPT_GUID)
        (assets / "Child.prefab").write_text(child_text, encoding="utf-8")
        (assets / "Child.prefab.meta").write_text(
            f"fileFormatVersion: 2\nguid: {_BOUNDARY_CHILD_GUID}\n",
            encoding="utf-8",
        )
        base_text = (
            YAML_HEADER
            + make_gameobject("10", "BaseRoot", ["20", "30"])
            + make_transform("20", "10")
            + make_monobehaviour("30", "10", guid=_BOUNDARY_SCRIPT_GUID)
            + make_prefab_instance("40", _BOUNDARY_CHILD_GUID)
        )
        base_path = assets / "Base.prefab"
        base_path.write_text(base_text, encoding="utf-8")
        (assets / "Base.prefab.meta").write_text(
            f"fileFormatVersion: 2\nguid: {_BOUNDARY_BASE_GUID}\n",
            encoding="utf-8",
        )
        return base_path

    def _run(self, total: int):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = self._build(root, total)
            pv = PrefabVariantService(project_root=root)
            rr = ReferenceResolverService(project_root=root)
            with patch(
                "prefab_sentinel.orchestrator_wiring.collect_project_guid_index",
                return_value={_BOUNDARY_CHILD_GUID: root / "Assets" / "Child.prefab"},
            ):
                # No explicit page_size — default literal participates.
                return inspect_wiring(pv, rr, target_path=str(base))

    def test_total_one_below_default_returns_single_page(self) -> None:
        below = _DEFAULT_INSPECT_WIRING_PAGE_SIZE - 1
        resp = self._run(below)
        self.assertEqual(below, resp.data["component_count"])
        self.assertEqual(below, resp.data["page_slice_length"])
        self.assertEqual("", resp.data["next_cursor"])

    def test_total_at_default_returns_single_page(self) -> None:
        at = _DEFAULT_INSPECT_WIRING_PAGE_SIZE
        resp = self._run(at)
        self.assertEqual(at, resp.data["component_count"])
        self.assertEqual(at, resp.data["page_slice_length"])
        self.assertEqual("", resp.data["next_cursor"])

    def test_total_one_above_default_emits_continuation(self) -> None:
        above = _DEFAULT_INSPECT_WIRING_PAGE_SIZE + 1
        resp = self._run(above)
        self.assertEqual(above, resp.data["component_count"])
        self.assertEqual(
            _DEFAULT_INSPECT_WIRING_PAGE_SIZE, resp.data["page_slice_length"],
        )
        self.assertEqual(
            f"{INSPECT_WIRING_CURSOR_PREFIX}{_DEFAULT_INSPECT_WIRING_PAGE_SIZE}",
            resp.data["next_cursor"],
        )
        # Sanity-check that the default literal is the one that fires
        # (mirrors the constant exposed by the orchestrator).
        self.assertEqual(
            INSPECT_WIRING_PAGE_SIZE_DEFAULT, _DEFAULT_INSPECT_WIRING_PAGE_SIZE,
        )


if __name__ == "__main__":
    unittest.main()
