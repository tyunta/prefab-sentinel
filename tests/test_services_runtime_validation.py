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
