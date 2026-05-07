"""Issue #197 — wiring inspection pagination contract.

The wiring orchestrator, the orchestrator façade, and the MCP tool surface
accept an opaque continuation token (``pos:<offset>``) and a bounded
``page_size`` (default 50, inclusive bounds [1, 500]) so a single response
stays within the MCP token cap on packaged scenes.

Tests construct synthetic prefab + child prefabs that produce a known total
of merged components, then exercise the page slicing, the cursor token
contract, and the page-independent diagnostic counts at the boundary.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.contracts import Severity
from prefab_sentinel.orchestrator import Phase1Orchestrator
from prefab_sentinel.orchestrator_wiring import (
    INSPECT_WIRING_CURSOR_PREFIX,
    INSPECT_WIRING_PAGE_SIZE_MAX,
    INSPECT_WIRING_PAGE_SIZE_MIN,
    inspect_wiring,
    validate_all_wiring,
)
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_monobehaviour,
    make_prefab_instance,
    make_transform,
)

_BASE_GUID = "11111111111111111111111111111111"
_CHILD_GUID = "22222222222222222222222222222222"
_SCRIPT_GUID = "33333333333333333333333333333333"


def _write_meta(path: Path, guid: str) -> None:
    path.write_text(f"fileFormatVersion: 2\nguid: {guid}\n", encoding="utf-8")


def _build_project_with_components(root: Path, total_components: int) -> Path:
    """Build a Base.prefab whose merged components list (root + nested) is
    ``total_components`` long, then return the path to Base.prefab.

    Layout: 1 component sits in the root prefab; the remaining
    ``total_components - 1`` components sit in a single nested Child.prefab
    referenced via PrefabInstance.  The merger pass concatenates the two
    component lists in that order.
    """
    assets = root / "Assets"
    assets.mkdir(parents=True, exist_ok=True)

    # Child.prefab — N-1 MonoBehaviours under one GameObject so the nested
    # merge produces a deterministic component count.
    nested_count = total_components - 1
    child_component_fids = [str(200 + i) for i in range(nested_count)]
    child_text = (
        YAML_HEADER
        + make_gameobject("100", "ChildObj", ["110"] + child_component_fids)
        + make_transform("110", "100")
    )
    for fid in child_component_fids:
        child_text += make_monobehaviour(fid, "100", guid=_SCRIPT_GUID)
    (assets / "Child.prefab").write_text(child_text, encoding="utf-8")
    _write_meta(assets / "Child.prefab.meta", _CHILD_GUID)

    # Base.prefab — 1 root MonoBehaviour + nested PrefabInstance pointing
    # at Child.prefab.
    base_text = (
        YAML_HEADER
        + make_gameobject("10", "BaseRoot", ["20", "30"])
        + make_transform("20", "10")
        + make_monobehaviour("30", "10", guid=_SCRIPT_GUID)
        + make_prefab_instance("40", _CHILD_GUID)
    )
    base_path = assets / "Base.prefab"
    base_path.write_text(base_text, encoding="utf-8")
    _write_meta(assets / "Base.prefab.meta", _BASE_GUID)
    return base_path


def _services_for_root(root: Path) -> tuple[PrefabVariantService, ReferenceResolverService]:
    pv = PrefabVariantService(project_root=root)
    rr = ReferenceResolverService(project_root=root)
    return pv, rr


class InspectWiringPaginationTests(unittest.TestCase):
    """Cursor + page_size contract on the wiring orchestrator."""

    TOTAL = 120
    PAGE = 50
    NESTED_GUID_INDEX_KEY = _CHILD_GUID

    def _run(self, root: Path, base_path: Path, **kwargs):
        pv, rr = _services_for_root(root)
        with patch(
            "prefab_sentinel.orchestrator_wiring.collect_project_guid_index",
            return_value={_CHILD_GUID: root / "Assets" / "Child.prefab"},
        ):
            return inspect_wiring(pv, rr, target_path=str(base_path), **kwargs)

    def test_first_page_returns_slice_and_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base_path = _build_project_with_components(root, self.TOTAL)
            resp = self._run(root, base_path, page_size=self.PAGE)
        self.assertTrue(resp.success)
        self.assertEqual("INSPECT_WIRING_RESULT", resp.code)
        self.assertEqual(self.TOTAL, resp.data["component_count"])
        self.assertEqual(self.PAGE, resp.data["page_slice_length"])
        self.assertEqual(self.PAGE, len(resp.data["components"]))
        self.assertEqual(
            f"{INSPECT_WIRING_CURSOR_PREFIX}{self.PAGE}", resp.data["next_cursor"],
        )

    def test_second_page_advances_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base_path = _build_project_with_components(root, self.TOTAL)
            resp = self._run(
                root, base_path,
                cursor=f"{INSPECT_WIRING_CURSOR_PREFIX}{self.PAGE}",
                page_size=self.PAGE,
            )
        self.assertEqual(self.PAGE, resp.data["page_slice_length"])
        self.assertEqual(
            f"{INSPECT_WIRING_CURSOR_PREFIX}{self.PAGE * 2}",
            resp.data["next_cursor"],
        )

    def test_last_page_clears_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base_path = _build_project_with_components(root, self.TOTAL)
            resp = self._run(
                root, base_path,
                cursor=f"{INSPECT_WIRING_CURSOR_PREFIX}{self.PAGE * 2}",
                page_size=self.PAGE,
            )
        # 120 total, position 100, page 50 → only 20 items remain
        self.assertEqual(20, resp.data["page_slice_length"])
        self.assertEqual("", resp.data["next_cursor"])

    def test_cursor_at_total_returns_empty_page(self) -> None:
        # ``_parse_inspect_wiring_cursor`` documents ``[0, total]`` as the
        # valid range — ``position == total`` is the terminal continuation
        # callers receive after consuming the last non-empty page. Pin the
        # zero-length-slice contract so a mutation to ``position > total``
        # (instead of ``position > total - 1``) flips the boundary.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base_path = _build_project_with_components(root, self.TOTAL)
            resp = self._run(
                root, base_path,
                cursor=f"{INSPECT_WIRING_CURSOR_PREFIX}{self.TOTAL}",
                page_size=self.PAGE,
            )
        self.assertTrue(resp.success)
        self.assertEqual("INSPECT_WIRING_RESULT", resp.code)
        self.assertEqual(self.TOTAL, resp.data["component_count"])
        self.assertEqual(0, resp.data["page_slice_length"])
        self.assertEqual([], resp.data["components"])
        self.assertEqual("", resp.data["next_cursor"])

    def test_invalid_cursor_token(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base_path = _build_project_with_components(root, self.TOTAL)
            resp = self._run(root, base_path, cursor="bogus")
        self.assertFalse(resp.success)
        self.assertEqual(Severity.ERROR, resp.severity)
        self.assertEqual("INSPECT_WIRING_INVALID_CURSOR", resp.code)
        self.assertIn("bogus", resp.message)

    def test_cursor_position_out_of_range(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base_path = _build_project_with_components(root, self.TOTAL)
            resp = self._run(
                root, base_path,
                cursor=f"{INSPECT_WIRING_CURSOR_PREFIX}9999",
            )
        self.assertFalse(resp.success)
        self.assertEqual("INSPECT_WIRING_INVALID_CURSOR", resp.code)
        self.assertIn(str(self.TOTAL), resp.message)

    def test_page_size_below_lower_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base_path = _build_project_with_components(root, self.TOTAL)
            resp = self._run(root, base_path, page_size=0)
        self.assertFalse(resp.success)
        self.assertEqual(Severity.ERROR, resp.severity)
        self.assertEqual("INSPECT_WIRING_PAGE_SIZE_OUT_OF_RANGE", resp.code)
        self.assertIn("0", resp.message)
        self.assertIn(str(INSPECT_WIRING_PAGE_SIZE_MIN), resp.message)
        self.assertIn(str(INSPECT_WIRING_PAGE_SIZE_MAX), resp.message)

    def test_page_size_above_upper_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base_path = _build_project_with_components(root, self.TOTAL)
            resp = self._run(
                root, base_path,
                page_size=INSPECT_WIRING_PAGE_SIZE_MAX + 1,
            )
        self.assertFalse(resp.success)
        self.assertEqual("INSPECT_WIRING_PAGE_SIZE_OUT_OF_RANGE", resp.code)

    def test_diagnostic_counts_are_page_independent(self) -> None:
        # Build a project that emits null references on every component.
        # Each MonoBehaviour carries a single null reference field; the
        # null-reference count must equal the merged component total
        # regardless of which page is requested.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            assets = root / "Assets"
            assets.mkdir(parents=True, exist_ok=True)
            nested_count = self.TOTAL - 1
            child_component_fids = [str(200 + i) for i in range(nested_count)]
            child_text = (
                YAML_HEADER
                + make_gameobject("100", "ChildObj", ["110"] + child_component_fids)
                + make_transform("110", "100")
            )
            for fid in child_component_fids:
                child_text += make_monobehaviour(
                    fid, "100", guid=_SCRIPT_GUID,
                    fields={"someRef": "{fileID: 0}"},
                )
            (assets / "Child.prefab").write_text(child_text, encoding="utf-8")
            _write_meta(assets / "Child.prefab.meta", _CHILD_GUID)

            base_text = (
                YAML_HEADER
                + make_gameobject("10", "BaseRoot", ["20", "30"])
                + make_transform("20", "10")
                + make_monobehaviour(
                    "30", "10", guid=_SCRIPT_GUID,
                    fields={"someRef": "{fileID: 0}"},
                )
                + make_prefab_instance("40", _CHILD_GUID)
            )
            base_path = assets / "Base.prefab"
            base_path.write_text(base_text, encoding="utf-8")
            _write_meta(assets / "Base.prefab.meta", _BASE_GUID)

            first = self._run(root, base_path, page_size=self.PAGE)
            last = self._run(
                root, base_path,
                cursor=f"{INSPECT_WIRING_CURSOR_PREFIX}{self.PAGE * 2}",
                page_size=self.PAGE,
            )

        self.assertEqual(
            first.data["null_reference_count"],
            last.data["null_reference_count"],
        )
        self.assertEqual(
            first.data["internal_broken_ref_count"],
            last.data["internal_broken_ref_count"],
        )
        self.assertEqual(
            first.data["duplicate_reference_count"],
            last.data["duplicate_reference_count"],
        )
        # Sanity: every merged component contributes one null reference.
        self.assertEqual(self.TOTAL, first.data["null_reference_count"])


class AggregatorWiringScanMaxPageSizeTests(unittest.TestCase):
    """``validate_all_wiring`` invokes ``inspect_wiring`` with the
    documented inclusive upper bound for ``page_size`` so each per-file
    scan returns the merged components list on a single page."""

    def test_aggregator_uses_max_page_size_per_call(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            assets = root / "Assets"
            assets.mkdir(parents=True, exist_ok=True)
            # Two .prefab files in scope so the aggregator iterates twice.
            for stem in ("A", "B"):
                text = (
                    YAML_HEADER
                    + make_gameobject("10", f"{stem}Root", ["20"])
                    + make_transform("20", "10")
                )
                (assets / f"{stem}.prefab").write_text(text, encoding="utf-8")
                _write_meta(assets / f"{stem}.prefab.meta", _SCRIPT_GUID[:31] + stem[0])

            pv, rr = _services_for_root(root)
            recorded: list[dict] = []
            real_inspect = inspect_wiring

            def _spy(prefab_variant, reference_resolver, target_path, **kwargs):
                recorded.append({"target_path": target_path, **kwargs})
                return real_inspect(
                    prefab_variant, reference_resolver,
                    target_path=target_path, **kwargs,
                )

            with patch(
                "prefab_sentinel.orchestrator_wiring.inspect_wiring",
                side_effect=_spy,
            ):
                resp = validate_all_wiring(pv, rr)

        self.assertTrue(resp.success)
        self.assertGreaterEqual(len(recorded), 2)
        for call in recorded:
            self.assertEqual(
                INSPECT_WIRING_PAGE_SIZE_MAX, call["page_size"],
                f"aggregator must pass the documented inclusive upper bound: {call}",
            )


class OrchestratorFacadeForwardsPaginationTests(unittest.TestCase):
    """``Phase1Orchestrator.inspect_wiring`` forwards cursor + page_size."""

    def test_facade_forwards_cursor_and_page_size(self) -> None:
        from unittest.mock import MagicMock

        orch = Phase1Orchestrator(
            reference_resolver=MagicMock(),
            prefab_variant=MagicMock(),
            runtime_validation=MagicMock(),
            serialized_object=MagicMock(),
        )
        with patch(
            "prefab_sentinel.orchestrator.orchestrator_wiring.inspect_wiring"
        ) as wired:
            orch.inspect_wiring(
                "X.prefab",
                udon_only=True,
                cursor=f"{INSPECT_WIRING_CURSOR_PREFIX}50",
                page_size=25,
            )
        wired.assert_called_once()
        kwargs = wired.call_args.kwargs
        self.assertEqual(True, kwargs["udon_only"])
        self.assertEqual(f"{INSPECT_WIRING_CURSOR_PREFIX}50", kwargs["cursor"])
        self.assertEqual(25, kwargs["page_size"])
