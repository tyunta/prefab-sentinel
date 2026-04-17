"""T30–T33: reporting-module edge case golden-file tests (issue #85).

Golden files live in ``tests/fixtures/reporting_edge_cases/*.golden.md``.
Each test builds the payload shape that ``benchmark_regression_report.main``
assembles and byte-compares the rendered markdown against the committed
golden file.  Rendering through ``_render_markdown_summary`` keeps the
tests decoupled from CLI argument parsing while still exercising the
production formatting path.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import benchmark_regression_report as brr  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reporting_edge_cases"

_COMMON_THRESHOLDS = {
    "avg_ratio_threshold": 1.2,
    "p90_ratio_threshold": 1.2,
    "min_absolute_delta_sec": 0.05,
}


def _empty_payload() -> dict[str, object]:
    return {
        "baseline_file_count": 0,
        "latest_file_count": 0,
        "baseline_scope_count": 0,
        "latest_scope_count": 0,
        "compared_scope_count": 0,
        "baseline_pinning_file": None,
        "baseline_pinning_applied_scopes": [],
        "missing_in_latest_scopes": [],
        "new_in_latest_scopes": [],
        "thresholds": _COMMON_THRESHOLDS,
        "regressed_scopes": [],
        "results": [],
    }


def _single_row_payload() -> dict[str, object]:
    return {
        "baseline_file_count": 1,
        "latest_file_count": 1,
        "baseline_scope_count": 1,
        "latest_scope_count": 1,
        "compared_scope_count": 1,
        "baseline_pinning_file": None,
        "baseline_pinning_applied_scopes": [],
        "missing_in_latest_scopes": [],
        "new_in_latest_scopes": [],
        "thresholds": _COMMON_THRESHOLDS,
        "regressed_scopes": [],
        "results": [
            {
                "scope": "sample/avatar/Assets",
                "status": "stable",
                "avg_ratio": 1.01,
                "p90_ratio": 1.02,
                "avg_delta_sec": 0.01,
                "p90_delta_sec": 0.02,
            }
        ],
    }


def _multi_scope_payload() -> dict[str, object]:
    return {
        "baseline_file_count": 2,
        "latest_file_count": 2,
        "baseline_scope_count": 2,
        "latest_scope_count": 2,
        "compared_scope_count": 2,
        "baseline_pinning_file": None,
        "baseline_pinning_applied_scopes": [],
        "missing_in_latest_scopes": [],
        "new_in_latest_scopes": [],
        "thresholds": _COMMON_THRESHOLDS,
        "regressed_scopes": ["sample/world/Assets"],
        "results": [
            {
                "scope": "sample/avatar/Assets",
                "status": "stable",
                "avg_ratio": 1.01,
                "p90_ratio": 1.02,
                "avg_delta_sec": 0.01,
                "p90_delta_sec": 0.02,
            },
            {
                "scope": "sample/world/Assets",
                "status": "regressed",
                "avg_ratio": 1.5,
                "p90_ratio": 1.6,
                "avg_delta_sec": 0.5,
                "p90_delta_sec": 0.7,
            },
        ],
    }


def _timezone_payload() -> dict[str, object]:
    return {
        "baseline_file_count": 2,
        "latest_file_count": 2,
        "baseline_scope_count": 1,
        "latest_scope_count": 1,
        "compared_scope_count": 1,
        "baseline_pinning_file": None,
        "baseline_pinning_applied_scopes": [],
        "missing_in_latest_scopes": [],
        "new_in_latest_scopes": [],
        "thresholds": _COMMON_THRESHOLDS,
        "regressed_scopes": [],
        "results": [
            {
                "scope": "sample/avatar/Assets",
                "status": "stable",
                "avg_ratio": 1.0,
                "p90_ratio": 1.0,
                "avg_delta_sec": 0.0,
                "p90_delta_sec": 0.0,
            }
        ],
    }


class ReportingEdgeCaseTests(unittest.TestCase):
    def _assert_golden(self, rendered: str, golden_name: str) -> None:
        golden = (_FIXTURES / golden_name).read_text(encoding="utf-8")
        self.assertEqual(
            golden,
            rendered,
            msg=f"Markdown output does not match {golden_name}",
        )

    def test_empty_benchmark_input(self) -> None:
        """T30: no baseline and no latest yields the empty-state golden output."""
        self._assert_golden(
            brr._render_markdown_summary(_empty_payload()),
            "empty.golden.md",
        )

    def test_single_row_input(self) -> None:
        """T31: a single stable scope renders one table row."""
        self._assert_golden(
            brr._render_markdown_summary(_single_row_payload()),
            "single_row.golden.md",
        )

    def test_multi_scope_merge(self) -> None:
        """T32: two scopes (one regressed, one stable) render deterministically."""
        self._assert_golden(
            brr._render_markdown_summary(_multi_scope_payload()),
            "multi_scope.golden.md",
        )

    def test_timezone_handling(self) -> None:
        """T33: ``_pick_latest_by_scope`` key uses the raw UTC string.

        The renderer itself doesn't touch timestamps, so we test the
        upstream sort key which is the TZ-sensitive surface in the module.
        Given two summaries with UTC ``Z`` timestamps one day apart, the
        picker must return the later one regardless of the runner's
        local TZ, and the rendered output must match the golden file
        byte-for-byte.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            early = {
                "scope": "sample/avatar/Assets",
                "generated_at_utc": "2026-02-11T10:00:00Z",
                "seconds": {"avg": 1.0, "p90": 1.0},
            }
            late = {
                "scope": "sample/avatar/Assets",
                "generated_at_utc": "2026-02-12T10:00:00Z",
                "seconds": {"avg": 1.0, "p90": 1.0},
            }
            early_path = root / "early.json"
            late_path = root / "late.json"
            early_path.write_text(json.dumps(early), encoding="utf-8")
            late_path.write_text(json.dumps(late), encoding="utf-8")

            # Exercise the sort path under a non-UTC local TZ.
            original_tz = os.environ.get("TZ")
            try:
                os.environ["TZ"] = "Asia/Tokyo"
                if hasattr(time, "tzset"):
                    time.tzset()

                picked = brr._pick_latest_by_scope([early_path, late_path])
                # Winner must be the later UTC timestamp regardless of local TZ.
                picked_path, _ = picked["sample/avatar/Assets"]
                self.assertEqual(late_path, picked_path)
            finally:
                if original_tz is None:
                    os.environ.pop("TZ", None)
                else:
                    os.environ["TZ"] = original_tz
                if hasattr(time, "tzset"):
                    time.tzset()

        # Golden comparison covers the rendered-markdown determinism.
        self._assert_golden(
            brr._render_markdown_summary(_timezone_payload()),
            "timezone.golden.md",
        )


if __name__ == "__main__":
    unittest.main()
