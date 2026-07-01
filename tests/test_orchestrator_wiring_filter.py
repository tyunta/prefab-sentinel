"""Tests for issue #227 — wiring summary mode and script-class filter.

The wiring orchestrator exposes two orthogonal flags on top of the
existing pagination contract:

* ``summary_only=True`` returns only the four diagnostic counts and
  suppresses both the per-component slice and the per-reference
  diagnostic list, keeping the response under the MCP token cap.
* ``script_filter=<identifier>`` narrows the merged component list to
  components whose recorded script class matches the supplied
  identifier. Bare class names and dotted fully-qualified names are
  both accepted; the comparison runs against the suffix after the last
  dot. A non-empty filter that matches nothing is reported as a
  warning (``INSPECT_WIRING_EMPTY_FILTER_RESULT``) so callers can
  distinguish "filter typo" from "target has no MonoBehaviours".
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.contracts import Severity
from prefab_sentinel.orchestrator_wiring import (
    _normalize_script_filter,
    inspect_wiring,
)
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_monobehaviour,
    make_transform,
)

# Three distinct script GUIDs so we can pin filtering by exact match
# against the resolved script_name (the .cs file stem).
_FOO_SCRIPT_GUID = "cccccccccccccccccccccccccccccccc"
_BAR_SCRIPT_GUID = "dddddddddddddddddddddddddddddddd"
_BAZ_SCRIPT_GUID = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"


def _write_meta(path: Path, guid: str) -> None:
    path.write_text(f"fileFormatVersion: 2\nguid: {guid}\n", encoding="utf-8")


def _build_three_class_fixture(root: Path) -> Path:
    """Build a Base.prefab with one MonoBehaviour per script GUID
    (FooBehaviour / BarBehaviour / BazBehaviour) and matching .cs +
    .cs.meta files under Assets/Scripts/. The .cs files are empty
    because the wiring scan only consults the GUID-to-name map; the
    scan never compiles or imports the C# source.

    Returns the Base.prefab path.
    """
    assets = root / "Assets"
    scripts = assets / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)

    (scripts / "FooBehaviour.cs").write_text("", encoding="utf-8")
    _write_meta(scripts / "FooBehaviour.cs.meta", _FOO_SCRIPT_GUID)
    (scripts / "BarBehaviour.cs").write_text("", encoding="utf-8")
    _write_meta(scripts / "BarBehaviour.cs.meta", _BAR_SCRIPT_GUID)
    (scripts / "BazBehaviour.cs").write_text("", encoding="utf-8")
    _write_meta(scripts / "BazBehaviour.cs.meta", _BAZ_SCRIPT_GUID)

    base_text = (
        YAML_HEADER
        + make_gameobject("10", "Root", ["20", "30", "40", "50"])
        + make_transform("20", "10")
        + make_monobehaviour("30", "10", guid=_FOO_SCRIPT_GUID)
        + make_monobehaviour(
            "40",
            "10",
            guid=_BAR_SCRIPT_GUID,
            fields={"targetRef": "{fileID: 0}"},
        )
        + make_monobehaviour("50", "10", guid=_BAZ_SCRIPT_GUID)
    )
    base_path = assets / "Base.prefab"
    base_path.write_text(base_text, encoding="utf-8")
    _write_meta(assets / "Base.prefab.meta", "11111111111111111111111111111111")
    return base_path


def _build_mixed_severity_fixture(root: Path) -> Path:
    assets = root / "Assets"
    scripts = assets / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)

    (scripts / "FooBehaviour.cs").write_text("", encoding="utf-8")
    _write_meta(scripts / "FooBehaviour.cs.meta", _FOO_SCRIPT_GUID)
    (scripts / "BarBehaviour.cs").write_text("", encoding="utf-8")
    _write_meta(scripts / "BarBehaviour.cs.meta", _BAR_SCRIPT_GUID)

    base_text = (
        YAML_HEADER
        + make_gameobject("10", "Root", ["20", "30", "40"])
        + make_transform("20", "10")
        + make_monobehaviour(
            "30",
            "10",
            guid=_FOO_SCRIPT_GUID,
            fields={"targetRef": "{fileID: 0}"},
        )
        + make_monobehaviour(
            "40",
            "10",
            guid=_BAR_SCRIPT_GUID,
            fields={"missingRef": "{fileID: 999}"},
        )
    )
    base_path = assets / "MixedSeverity.prefab"
    base_path.write_text(base_text, encoding="utf-8")
    _write_meta(assets / "MixedSeverity.prefab.meta", "33333333333333333333333333333333")
    return base_path


def _build_duplicate_reference_fixture(root: Path) -> Path:
    assets = root / "Assets"
    scripts = assets / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)

    (scripts / "FooBehaviour.cs").write_text("", encoding="utf-8")
    _write_meta(scripts / "FooBehaviour.cs.meta", _FOO_SCRIPT_GUID)

    base_text = (
        YAML_HEADER
        + make_gameobject("10", "Root", ["20", "30"])
        + make_transform("20", "10")
        + make_monobehaviour(
            "30",
            "10",
            guid=_FOO_SCRIPT_GUID,
            fields={
                "firstA": "{fileID: 60}",
                "firstB": "{fileID: 60}",
                "secondA": "{fileID: 70}",
                "secondB": "{fileID: 70}",
            },
        )
        + make_gameobject("60", "TargetA", ["61"])
        + make_transform("61", "60")
        + make_gameobject("70", "TargetB", ["71"])
        + make_transform("71", "70")
    )
    base_path = assets / "DuplicateRefs.prefab"
    base_path.write_text(base_text, encoding="utf-8")
    _write_meta(assets / "DuplicateRefs.prefab.meta", "22222222222222222222222222222222")
    return base_path


def _build_mixed_duplicate_reference_fixture(root: Path) -> Path:
    assets = root / "Assets"
    scripts = assets / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)

    (scripts / "FooBehaviour.cs").write_text("", encoding="utf-8")
    _write_meta(scripts / "FooBehaviour.cs.meta", _FOO_SCRIPT_GUID)

    base_text = (
        YAML_HEADER
        + make_gameobject("10", "Root", ["20", "30", "40"])
        + make_transform("20", "10")
        + make_monobehaviour(
            "30",
            "10",
            guid=_FOO_SCRIPT_GUID,
            fields={
                "sameA": "{fileID: 60}",
                "sameB": "{fileID: 60}",
            },
        )
        + make_monobehaviour(
            "40",
            "10",
            guid=_FOO_SCRIPT_GUID,
            fields={"crossA": "{fileID: 60}"},
        )
        + make_gameobject("60", "TargetA", ["61"])
        + make_transform("61", "60")
    )
    base_path = assets / "MixedDuplicateRefs.prefab"
    base_path.write_text(base_text, encoding="utf-8")
    _write_meta(assets / "MixedDuplicateRefs.prefab.meta", "44444444444444444444444444444444")
    return base_path


def _services_for_root(root: Path) -> tuple[PrefabVariantService, ReferenceResolverService]:
    pv = PrefabVariantService(project_root=root)
    rr = ReferenceResolverService(project_root=root)
    return pv, rr


class FilterNormalizerTests(unittest.TestCase):
    """Pure-function ``_normalize_script_filter`` contract tests."""

    def test_empty_input_returns_empty_string(self) -> None:
        """An empty filter is the no-op default; the normalizer leaves it
        unchanged so ``inspect_wiring`` keeps its default behaviour.
        """
        self.assertEqual("", _normalize_script_filter(""))

    def test_dotted_input_returns_suffix_after_last_dot(self) -> None:
        """A fully-qualified type name normalises to the trailing segment
        so it matches the bare-class-name shape that script_name carries.
        """
        self.assertEqual(
            "AvatarSync",
            _normalize_script_filter("VRC.Avatar.AvatarSync"),
        )

    def test_bare_class_name_passes_through(self) -> None:
        """A non-dotted identifier is returned identity so the existing
        bare-class-name workflow is preserved.
        """
        self.assertEqual(
            "AvatarSync",
            _normalize_script_filter("AvatarSync"),
        )


class InspectWiringFilterAndSummaryTests(unittest.TestCase):
    """Behavioural contract for the combined filter / summary surface."""

    def _run(self, root: Path, base_path: Path, **kwargs):
        pv, rr = _services_for_root(root)
        target_path = base_path.relative_to(root).as_posix()
        # Patch the GUID index to expose the three .cs files as the
        # script-name resolution source. The path values are not used
        # for content reads; only the stems and suffixes matter.
        guid_index = {
            _FOO_SCRIPT_GUID: root / "Assets" / "Scripts" / "FooBehaviour.cs",
            _BAR_SCRIPT_GUID: root / "Assets" / "Scripts" / "BarBehaviour.cs",
            _BAZ_SCRIPT_GUID: root / "Assets" / "Scripts" / "BazBehaviour.cs",
        }
        with patch(
            "prefab_sentinel.orchestrator_wiring.collect_project_guid_index",
            return_value=guid_index,
        ):
            return inspect_wiring(pv, rr, target_path=target_path, **kwargs)

    def test_rejects_windows_absolute_target_path(self) -> None:
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pv, rr = _services_for_root(root)
            target_path = r"C:\outside\Base.prefab"
            response = inspect_wiring(pv, rr, target_path=target_path)

        assert_error_envelope(
            response,
            code="INSPECT_WIRING_INVALID_TARGET_PATH",
            message_match=r"project-root-relative.*project_root",
            data={"target_path": target_path, "read_only": True},
        )

    def test_rejects_traversal_target_path(self) -> None:
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _build_three_class_fixture(root)
            pv, rr = _services_for_root(root)
            target_path = "Assets/../Assets/Base.prefab"
            response = inspect_wiring(pv, rr, target_path=target_path)

        assert_error_envelope(
            response,
            code="INSPECT_WIRING_INVALID_TARGET_PATH",
            message_match=r"project-root-relative.*project_root",
            data={"target_path": target_path, "read_only": True},
        )

    def test_default_behaviour_returns_full_payload(self) -> None:
        """Issue #227 — neither flag supplied means the existing wiring
        contract is preserved: the merged component slice is present and
        the four diagnostic counts are populated.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            resp = self._run(root, base)
        self.assertEqual(3, resp.data["component_count"])
        self.assertEqual(3, len(resp.data["components"]))
        # Diagnostic counts are present in default mode.
        for key in (
            "null_reference_count",
            "internal_broken_ref_count",
            "duplicate_reference_count",
        ):
            self.assertIn(key, resp.data)

    def test_clean_filter_reports_out_of_scope_counts_without_raising_severity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            resp = self._run(root, base, script_filter="FooBehaviour")

        self.assertEqual(
            (True, Severity.INFO, 0, 1, 1),
            (
                resp.success,
                resp.severity,
                resp.data.get("diagnostic_counts", {}).get("filtered", {}).get("total"),
                resp.data.get("diagnostic_counts", {}).get("out_of_scope", {}).get("total"),
                len(resp.diagnostics),
            ),
        )
        self.assertEqual([], resp.data.get("filtered_diagnostics"))
        self.assertNotIn("out_of_scope_diagnostics", resp.data)

    def test_clean_filter_opt_in_includes_out_of_scope_rows(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            resp = self._run(
                root,
                base,
                script_filter="FooBehaviour",
                include_out_of_scope_diagnostics=True,
            )

        self.assertEqual(
            (1, "Null reference: Root.targetRef"),
            (
                len(resp.data.get("out_of_scope_diagnostics", [])),
                resp.data.get("out_of_scope_diagnostics", [{}])[0].get("code"),
            ),
        )

    def test_unfiltered_opt_in_does_not_create_out_of_scope_partition(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            resp = self._run(root, base, include_out_of_scope_diagnostics=True)

        self.assertEqual((Severity.WARNING, 1), (resp.severity, len(resp.diagnostics)))
        self.assertNotIn("diagnostic_counts", resp.data)
        self.assertNotIn("filtered_diagnostics", resp.data)
        self.assertNotIn("out_of_scope_diagnostics", resp.data)

    def test_warning_filter_classifies_filtered_diagnostic_as_known(self) -> None:
        from prefab_sentinel.diagnostics_baseline import DiagnosticsBaseline

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            target_path = base.relative_to(root).as_posix()
            known_key = f"inspect_wiring:null_reference:{target_path}:40:targetRef"
            baseline = DiagnosticsBaseline(
                known_diagnostics=(known_key,),
                path=str(root / "config" / "diagnostics_baseline.json"),
                status="loaded",
            )
            resp = self._run(
                root,
                base,
                script_filter="BarBehaviour",
                diagnostics_baseline=baseline,
            )

        self.assertEqual(
            (Severity.WARNING, 1, 1, 0, [known_key]),
            (
                resp.severity,
                resp.data.get("diagnostic_counts", {}).get("filtered", {}).get("total"),
                resp.data.get("diagnostics_baseline", {}).get("known_count"),
                resp.data.get("diagnostics_baseline", {}).get("new_count"),
                [
                    item["key"]
                    for item in resp.data.get("diagnostics_baseline", {}).get("known", [])
                ],
            ),
        )


    def test_filtered_warning_keeps_warning_severity_with_out_of_scope_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_mixed_severity_fixture(root)
            resp = self._run(root, base, script_filter="FooBehaviour")

        self.assertEqual(
            {
                "response": (True, Severity.WARNING),
                "filtered_counts": {"total": 1, "warning": 1, "error": 0},
                "out_of_scope_counts": {"total": 1, "warning": 0, "error": 1},
                "filtered_rows": [("warning", "Null reference: Root.targetRef")],
            },
            {
                "response": (resp.success, resp.severity),
                "filtered_counts": {
                    "total": resp.data["diagnostic_counts"]["filtered"]["total"],
                    "warning": resp.data["diagnostic_counts"]["filtered"]["warning"],
                    "error": resp.data["diagnostic_counts"]["filtered"]["error"],
                },
                "out_of_scope_counts": {
                    "total": resp.data["diagnostic_counts"]["out_of_scope"]["total"],
                    "warning": resp.data["diagnostic_counts"]["out_of_scope"]["warning"],
                    "error": resp.data["diagnostic_counts"]["out_of_scope"]["error"],
                },
                "filtered_rows": [
                    (row["severity"], row["code"])
                    for row in resp.data.get("filtered_diagnostics", [])
                ],
            },
        )


    def test_duplicate_reference_keys_include_target_file_id(self) -> None:
        from prefab_sentinel.diagnostics_baseline import DiagnosticsBaseline

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_duplicate_reference_fixture(root)
            target_path = base.relative_to(root).as_posix()
            baseline = DiagnosticsBaseline(
                known_diagnostics=(),
                path=str(root / "config" / "diagnostics_baseline.json"),
                status="loaded",
            )
            pv, rr = _services_for_root(root)
            with patch(
                "prefab_sentinel.orchestrator_wiring.collect_project_guid_index",
                return_value={
                    _FOO_SCRIPT_GUID: root / "Assets" / "Scripts" / "FooBehaviour.cs",
                },
            ):
                resp = inspect_wiring(
                    pv,
                    rr,
                    target_path=target_path,
                    diagnostics_baseline=baseline,
                )

        duplicate_keys = [
            item["key"]
            for item in resp.data.get("diagnostics_baseline", {}).get("new", [])
            if item["data"]["category"] == "duplicate_reference"
        ]
        self.assertEqual(
            [
                f"inspect_wiring:duplicate_reference:{target_path}:30:same-component:fileID:60",
                f"inspect_wiring:duplicate_reference:{target_path}:30:same-component:fileID:70",
            ],
            duplicate_keys,
        )


    def test_duplicate_reference_keys_include_branch_kind_for_same_target(self) -> None:
        from prefab_sentinel.diagnostics_baseline import DiagnosticsBaseline

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_mixed_duplicate_reference_fixture(root)
            target_path = base.relative_to(root).as_posix()
            baseline = DiagnosticsBaseline(
                known_diagnostics=(),
                path=str(root / "config" / "diagnostics_baseline.json"),
                status="loaded",
            )
            pv, rr = _services_for_root(root)
            with patch(
                "prefab_sentinel.orchestrator_wiring.collect_project_guid_index",
                return_value={
                    _FOO_SCRIPT_GUID: root / "Assets" / "Scripts" / "FooBehaviour.cs",
                },
            ):
                resp = inspect_wiring(
                    pv,
                    rr,
                    target_path=target_path,
                    diagnostics_baseline=baseline,
                )

        duplicate_keys = [
            item["key"]
            for item in resp.data.get("diagnostics_baseline", {}).get("new", [])
            if item["data"]["category"] == "duplicate_reference"
        ]
        self.assertEqual(
            [
                f"inspect_wiring:duplicate_reference:{target_path}:30:same-component:fileID:60",
                f"inspect_wiring:duplicate_reference:{target_path}:30:cross-component:fileID:60",
            ],
            duplicate_keys,
        )

    def test_summary_filter_keeps_counts_without_detail_arrays(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            resp = self._run(
                root,
                base,
                script_filter="FooBehaviour",
                summary_only=True,
            )

        self.assertNotIn("components", resp.data)
        self.assertNotIn("filtered_diagnostics", resp.data)
        self.assertNotIn("out_of_scope_diagnostics", resp.data)
        self.assertEqual(
            {"filtered_total": 0, "out_of_scope_total": 1},
            {
                "filtered_total": resp.data.get("diagnostic_counts", {})
                .get("filtered", {})
                .get("total"),
                "out_of_scope_total": resp.data.get("diagnostic_counts", {})
                .get("out_of_scope", {})
                .get("total"),
            },
        )

    def test_bare_class_name_filter_narrows_to_one_component(self) -> None:
        """Issue #227 — a bare class name filter matches one of three
        scripts; the surviving entry is the FooBehaviour one and the
        merged count drops to one.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            resp = self._run(root, base, script_filter="FooBehaviour")
        self.assertEqual(1, resp.data["component_count"])
        self.assertEqual(1, len(resp.data["components"]))
        self.assertEqual("FooBehaviour", resp.data["components"][0]["script_name"])

    def test_dotted_fq_name_filter_narrows_by_suffix(self) -> None:
        """Issue #227 — a dotted fully-qualified name filter normalises
        to its suffix and matches the same component as the bare name.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            resp = self._run(
                root, base, script_filter="MyNamespace.FooBehaviour",
            )
        self.assertEqual(1, resp.data["component_count"])
        self.assertEqual("FooBehaviour", resp.data["components"][0]["script_name"])

    def test_no_match_filter_surfaces_empty_filter_warning(self) -> None:
        """Issue #227 — a filter that matches no component is reported
        with a distinct warning code so callers can distinguish "filter
        spelled wrong" from "target has no MonoBehaviours".
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            resp = self._run(
                root, base, script_filter="NonexistentBehaviour",
            )
        self.assertEqual(Severity.WARNING, resp.severity)
        self.assertEqual("INSPECT_WIRING_EMPTY_FILTER_RESULT", resp.code)
        self.assertIn("NonexistentBehaviour", resp.message)
        self.assertEqual(
            (0, 0, 0),
            (
                resp.data["component_count"],
                resp.data["diagnostic_counts"]["filtered"]["total"],
                resp.data["diagnostic_counts"]["out_of_scope"]["total"],
            ),
        )

    def test_summary_mode_suppresses_slice_and_pagination(self) -> None:
        """Issue #227 — summary mode keeps the response under the token
        cap by suppressing the per-component slice and pagination
        metadata; the four diagnostic counts remain.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            resp = self._run(root, base, summary_only=True)
        self.assertNotIn("components", resp.data)
        self.assertNotIn("page_slice_length", resp.data)
        self.assertNotIn("next_cursor", resp.data)
        self.assertEqual(3, resp.data["component_count"])
        for key in (
            "null_reference_count",
            "internal_broken_ref_count",
            "duplicate_reference_count",
        ):
            self.assertIn(key, resp.data)

    def test_summary_mode_preserves_count_totals(self) -> None:
        """Issue #227 — the four counts in summary mode equal the four
        counts in full mode for the same fixture.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            full = self._run(root, base)
            summary = self._run(root, base, summary_only=True)
        for key in (
            "component_count",
            "null_reference_count",
            "internal_broken_ref_count",
            "duplicate_reference_count",
        ):
            self.assertEqual(full.data[key], summary.data[key])

    def test_combined_filter_and_summary_returns_filtered_counts(self) -> None:
        """Issue #227 — when both flags are set, the four counts reflect
        only the filtered subset's diagnostics; the per-component slice
        and pagination metadata stay suppressed.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = _build_three_class_fixture(root)
            resp = self._run(
                root, base,
                script_filter="FooBehaviour",
                summary_only=True,
            )
        self.assertEqual(1, resp.data["component_count"])
        self.assertNotIn("components", resp.data)
