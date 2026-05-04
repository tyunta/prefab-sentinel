"""Tests for ``RuntimeValidationService.classify_errors`` reshape (issue #89).

Pins the data-key contract after the rename:

- ``matched_issue_count`` -> ``count_total``
- ``categories`` -> ``count_by_category``

And the severity pin:

- ``UDON_NULLREF`` matches must surface at ``severity="critical"``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.contracts import Severity
from prefab_sentinel.services.runtime_validation import RuntimeValidationService


class RuntimeValidationClassifyTests(unittest.TestCase):
    def test_udon_nullref_returns_critical(self) -> None:
        """T58: a line matching UDON_NULLREF must surface at severity=critical."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            ["NullReferenceException in UdonBehaviour.MyEvent"],
        )
        self.assertEqual(Severity.CRITICAL, resp.severity)

    def test_data_has_count_total_and_count_by_category(self) -> None:
        """T59: response data must expose ``count_total`` + ``count_by_category``
        and must not carry the old ``matched_issue_count`` / ``categories`` keys."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            [
                "Broken PPtr in file A",
                "NullReferenceException in UdonBehaviour.B",
            ],
        )
        self.assertIn("count_total", resp.data)
        self.assertIn("count_by_category", resp.data)
        self.assertNotIn("matched_issue_count", resp.data)
        self.assertNotIn("categories", resp.data)

    def test_count_total_reflects_match_count(self) -> None:
        """T60: ``count_total`` equals the number of lines that matched a
        known category (kept distinct from the size of
        ``count_by_category``, which counts *categories* not *hits*)."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            [
                "Broken PPtr in file A",
                "Broken PPtr in file B",
                "NullReferenceException in UdonBehaviour.C",
                "unrelated log line",
            ],
        )
        self.assertEqual(3, resp.data["count_total"])

    def test_count_by_category_groups_per_category(self) -> None:
        """T61: ``count_by_category`` maps each category to its hit count."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            [
                "Broken PPtr in file A",
                "Broken PPtr in file B",
                "NullReferenceException in UdonBehaviour.C",
            ],
        )
        by_cat = resp.data["count_by_category"]
        self.assertEqual(2, by_cat.get("BROKEN_PPTR"))
        self.assertEqual(1, by_cat.get("UDON_NULLREF"))

    def test_empty_input_returns_zero_total(self) -> None:
        """T62: an empty log yields ``count_total == 0`` and empty
        ``count_by_category``."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors([])
        self.assertEqual(0, resp.data["count_total"])
        self.assertEqual({}, resp.data["count_by_category"])
        self.assertTrue(resp.success)

    def test_unmatched_lines_do_not_inflate_total(self) -> None:
        """T63: lines that do not match any pattern must not contribute to
        ``count_total`` or ``count_by_category``."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            [
                "just a log line",
                "another unrelated message",
            ],
        )
        self.assertEqual(0, resp.data["count_total"])
        self.assertEqual({}, resp.data["count_by_category"])


class RuntimeClassificationSeverityBandTests(unittest.TestCase):
    """C1 — pin the four severity bands (critical / error / warning / info)
    by value plus the corresponding per-category count by value.

    Issue #145: every distinct severity band must produce a deterministic
    severity value and a deterministic per-category count so a downgrade
    mutation (e.g. CRITICAL -> ERROR) fails an equality assertion.
    """

    def test_critical_band_pins_severity_and_udon_nullref_count(self) -> None:
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            ["NullReferenceException in UdonBehaviour.MyEvent"],
        )
        self.assertEqual(Severity.CRITICAL, resp.severity)
        self.assertEqual(1, resp.data["count_by_category"]["UDON_NULLREF"])
        self.assertEqual(1, resp.data["categories_by_severity"]["critical"])

    def test_error_band_pins_severity_and_broken_pptr_count(self) -> None:
        svc = RuntimeValidationService()
        resp = svc.classify_errors(["Broken PPtr in file Foo"])
        self.assertEqual(Severity.ERROR, resp.severity)
        self.assertEqual(1, resp.data["count_by_category"]["BROKEN_PPTR"])
        self.assertEqual(1, resp.data["categories_by_severity"]["error"])

    def test_warning_band_pins_severity_and_eventsystem_count(self) -> None:
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            ["There can be only one active EventSystem in the scene"],
        )
        self.assertEqual(Severity.WARNING, resp.severity)
        self.assertEqual(
            1, resp.data["count_by_category"]["DUPLICATE_EVENTSYSTEM"]
        )
        self.assertEqual(
            1, resp.data["categories_by_severity"]["warning"]
        )

    def test_info_band_pins_severity_and_zero_total_for_unmatched_input(self) -> None:
        """Issue #160 documents this row: unmatched input rolls up to
        the informational band with ``count_total == 0`` because the
        info band has no associated log pattern."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(["unrelated runtime log line"])
        # No pattern matches; severity rolls up to INFO and count_total = 0.
        self.assertEqual(Severity.INFO, resp.severity)
        self.assertEqual(0, resp.data["count_total"])
        self.assertEqual({}, resp.data["count_by_category"])
        # No category was incremented in any band.
        self.assertEqual(0, resp.data["categories_by_severity"]["critical"])
        self.assertEqual(0, resp.data["categories_by_severity"]["error"])
        self.assertEqual(0, resp.data["categories_by_severity"]["warning"])

    def test_mixed_band_rolls_up_to_maximum_severity(self) -> None:
        """Issue #145 mixed-band row: when a single log buffer contains
        warning + error + critical hits, the rollup severity equals the
        maximum (``critical``); the per-band counter exposes each band's
        contribution by exact value, and ``count_total`` equals the
        sum of all hits across bands."""
        svc = RuntimeValidationService()
        resp = svc.classify_errors(
            [
                "There can be only one active EventSystem in the scene",
                "Broken PPtr in file Foo",
                "Broken PPtr in file Bar",
                "NullReferenceException in UdonBehaviour.Baz",
            ],
        )
        self.assertEqual(Severity.CRITICAL, resp.severity)
        self.assertEqual(4, resp.data["count_total"])
        self.assertEqual(1, resp.data["categories_by_severity"]["critical"])
        self.assertEqual(2, resp.data["categories_by_severity"]["error"])
        self.assertEqual(1, resp.data["categories_by_severity"]["warning"])
        # ``count_by_category`` carries each pattern's hits.
        self.assertEqual(2, resp.data["count_by_category"]["BROKEN_PPTR"])
        self.assertEqual(1, resp.data["count_by_category"]["UDON_NULLREF"])
        self.assertEqual(1, resp.data["count_by_category"]["DUPLICATE_EVENTSYSTEM"])

    def test_truncation_caps_diagnostics_and_records_overflow(self) -> None:
        """Issue #145 truncation row: when the total hit count exceeds
        ``max_diagnostics``, the diagnostics list is capped at that
        value, ``returned_diagnostics`` equals the cap,
        ``truncated_diagnostics`` equals the overflow, and
        ``count_total`` still equals every hit (no diagnostic loss in
        the counter)."""
        svc = RuntimeValidationService()
        log_lines = [f"Broken PPtr in file {n}" for n in range(7)]
        resp = svc.classify_errors(log_lines, max_diagnostics=3)
        self.assertEqual(7, resp.data["count_total"])
        self.assertEqual(3, len(resp.diagnostics))
        self.assertEqual(3, resp.data["returned_diagnostics"])
        self.assertEqual(4, resp.data["truncated_diagnostics"])


class RuntimeAssertNoCriticalErrorsTests(unittest.TestCase):
    """Issue #145 — pin every outcome of
    ``assert_no_critical_errors`` by code, severity, and exact
    per-band counts."""

    def _classify(
        self,
        log_lines: list[str],
    ) -> object:
        svc = RuntimeValidationService()
        return svc.classify_errors(log_lines)

    def test_clean_classification_returns_run_assert_ok(self) -> None:
        """No hits returns ``RUN_ASSERT_OK`` with all bands at zero."""
        from prefab_sentinel.services.runtime_validation.classification import (  # noqa: PLC0415
            assert_no_critical_errors,
        )

        classification = self._classify([])
        outcome = assert_no_critical_errors(classification)
        self.assertTrue(outcome.success)
        self.assertEqual("RUN_ASSERT_OK", outcome.code)
        self.assertEqual(0, outcome.data["critical_count"])
        self.assertEqual(0, outcome.data["error_count"])
        self.assertEqual(0, outcome.data["warning_count"])

    def test_warnings_not_allowed_returns_run_warnings(self) -> None:
        """A warning hit with ``allow_warnings=False`` returns
        ``RUN_WARNINGS`` at ``severity=warning`` and the warning
        counter at one."""
        from prefab_sentinel.services.runtime_validation.classification import (  # noqa: PLC0415
            assert_no_critical_errors,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        classification = self._classify(
            ["There can be only one active EventSystem in the scene"]
        )
        outcome = assert_no_critical_errors(classification, allow_warnings=False)
        assert_error_envelope(outcome, code="RUN_WARNINGS", severity="warning")
        self.assertEqual(0, outcome.data["critical_count"])
        self.assertEqual(0, outcome.data["error_count"])
        self.assertEqual(1, outcome.data["warning_count"])
        self.assertFalse(outcome.data["allow_warnings"])

    def test_warnings_allowed_returns_run_assert_ok(self) -> None:
        """A warning hit with ``allow_warnings=True`` returns
        ``RUN_ASSERT_OK`` and the warning counter still at one (the
        assertion bypass does not silence the count)."""
        from prefab_sentinel.services.runtime_validation.classification import (  # noqa: PLC0415
            assert_no_critical_errors,
        )

        classification = self._classify(
            ["There can be only one active EventSystem in the scene"]
        )
        outcome = assert_no_critical_errors(classification, allow_warnings=True)
        self.assertTrue(outcome.success)
        self.assertEqual("RUN_ASSERT_OK", outcome.code)
        self.assertEqual(1, outcome.data["warning_count"])
        self.assertTrue(outcome.data["allow_warnings"])

    def test_error_band_returns_run001_at_severity_error(self) -> None:
        """An error hit (no critical) returns ``RUN001`` at
        ``severity=error`` with the error counter at one."""
        from prefab_sentinel.services.runtime_validation.classification import (  # noqa: PLC0415
            assert_no_critical_errors,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        classification = self._classify(["Broken PPtr in file Foo"])
        outcome = assert_no_critical_errors(classification)
        assert_error_envelope(outcome, code="RUN001", severity="error")
        self.assertEqual(0, outcome.data["critical_count"])
        self.assertEqual(1, outcome.data["error_count"])
        self.assertEqual(0, outcome.data["warning_count"])

    def test_critical_band_returns_run001_at_severity_critical(self) -> None:
        """A critical hit returns ``RUN001`` at ``severity=critical``
        with the critical counter at one (the highest failing band
        wins the severity assignment)."""
        from prefab_sentinel.services.runtime_validation.classification import (  # noqa: PLC0415
            assert_no_critical_errors,
        )
        from tests._assertion_helpers import assert_error_envelope  # noqa: PLC0415

        classification = self._classify(
            ["NullReferenceException in UdonBehaviour.MyEvent"]
        )
        outcome = assert_no_critical_errors(classification)
        assert_error_envelope(outcome, code="RUN001", severity="critical")
        self.assertEqual(1, outcome.data["critical_count"])
        self.assertEqual(0, outcome.data["error_count"])


def _make_project_root(base: Path, name: str = "projA") -> Path:
    """Create a minimal Unity-style project under ``base/name`` (has ``Assets/``)."""
    root = base / name
    (root / "Assets").mkdir(parents=True, exist_ok=True)
    (root / "Logs").mkdir(parents=True, exist_ok=True)
    return root


class CollectUnityConsoleDecodeTests(unittest.TestCase):
    """T-95-A: ``collect_unity_console`` returns a warning-severity
    ``success_response`` with ``RUN_LOG_DECODE_WARN`` and empty log lines
    when the log file contains an invalid UTF-8 sequence."""

    def test_unicode_decode_error_returns_warn_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = _make_project_root(Path(temp_dir))
            log_path = root / "Logs" / "Editor.log"
            log_path.write_bytes(b"\x81\x00\xff\xfe invalid utf-8")

            svc = RuntimeValidationService(project_root=root)
            resp = svc.collect_unity_console()

            self.assertEqual("RUN_LOG_DECODE_WARN", resp.code)
            self.assertEqual(Severity.WARNING, resp.severity)
            self.assertTrue(resp.success)
            self.assertEqual([], resp.data["log_lines"])
            self.assertEqual(0, resp.data["line_count"])
            self.assertTrue(resp.data["log_path"].endswith("Logs/Editor.log"))


class CollectUnityConsoleContainmentTests(unittest.TestCase):
    """T-96-A/B/C: ``collect_unity_console`` rejects log paths that
    resolve outside the configured runtime root."""

    def test_relative_path_escaping_root_is_rejected(self) -> None:
        """T-96-A: ``../projB/Logs/Editor.log`` is rejected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir).resolve()
            root_a = _make_project_root(base, "projA")
            root_b = _make_project_root(base, "projB")
            escaped_log = root_b / "Logs" / "Editor.log"
            escaped_log.write_text("outside content\n", encoding="utf-8")

            svc = RuntimeValidationService(project_root=root_a)
            resp = svc.collect_unity_console(log_file="../projB/Logs/Editor.log")

            self.assertFalse(resp.success)
            self.assertEqual("RUN_CONFIG_ERROR", resp.code)
            self.assertEqual(Severity.ERROR, resp.severity)
            self.assertEqual("../projB/Logs/Editor.log", resp.data["log_file"])
            self.assertEqual(str(root_a.resolve()), resp.data["runtime_root"])
            self.assertFalse(resp.data["executed"])

    def test_relative_path_inside_root_is_accepted(self) -> None:
        """T-96-B: ``Logs/Editor.log`` (inside root) decodes normally."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir).resolve()
            root_a = _make_project_root(base, "projA")
            (root_a / "Logs" / "Editor.log").write_text(
                "alpha\nbeta\n", encoding="utf-8"
            )

            svc = RuntimeValidationService(project_root=root_a)
            resp = svc.collect_unity_console(log_file="Logs/Editor.log")

            self.assertTrue(resp.success)
            self.assertEqual("RUN_LOG_COLLECTED", resp.code)
            self.assertEqual(["alpha", "beta"], resp.data["log_lines"])

    def test_absolute_path_outside_root_is_rejected(self) -> None:
        """T-96-C: absolute path outside root is rejected and not decoded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir).resolve()
            root_a = _make_project_root(base, "projA")
            outside = base / "outside.log"
            outside.write_text("readable but outside\n", encoding="utf-8")

            svc = RuntimeValidationService(project_root=root_a)
            resp = svc.collect_unity_console(log_file=str(outside))

            self.assertFalse(resp.success)
            self.assertEqual("RUN_CONFIG_ERROR", resp.code)
            self.assertEqual(Severity.ERROR, resp.severity)
            self.assertFalse(resp.data["executed"])

    def test_symlink_escaping_root_is_rejected(self) -> None:
        """T-96-D: a symlink inside the root whose real path lies outside
        is rejected. ``Path.resolve()`` follows symlinks before containment
        is checked, so the escape is caught before any file read."""
        if not hasattr(os, "symlink"):
            self.skipTest("platform does not support os.symlink")
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir).resolve()
            root_a = _make_project_root(base, "projA")
            outside = base / "outside.log"
            outside.write_text("outside content\n", encoding="utf-8")

            symlink_path = root_a / "Logs" / "escape.log"
            try:
                os.symlink(outside, symlink_path)
            except (OSError, NotImplementedError) as exc:
                # Some CI sandboxes refuse symlink creation; in that
                # case the escape vector is not reachable and the
                # test is not applicable.
                self.skipTest(f"symlink creation not permitted: {exc}")

            svc = RuntimeValidationService(project_root=root_a)
            resp = svc.collect_unity_console(log_file="Logs/escape.log")

            self.assertFalse(resp.success)
            self.assertEqual("RUN_CONFIG_ERROR", resp.code)
            self.assertEqual(Severity.ERROR, resp.severity)
            self.assertFalse(resp.data["executed"])


if __name__ == "__main__":
    unittest.main()
