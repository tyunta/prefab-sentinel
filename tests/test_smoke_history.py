from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.smoke_history import (
    _build_target_stats,
    _build_timeout_profiles,
    _case_to_row,
    _compute_applied_assertion_metrics,
    _compute_code_assertion_metrics,
    _compute_observed_timeout_metrics,
    _compute_observed_timeout_metrics_by_target,
    _compute_profile_timeout_metrics,
    _compute_profile_timeout_metrics_by_target,
    _expand_inputs,
    _is_smoke_batch_summary,
    _percentile,
    _render_markdown_summary,
    _round_up_timeout,
    _to_bool,
    _to_float,
    _to_int,
    main as smoke_history_main,
)


class TypeConverterTests(unittest.TestCase):
    def test_to_float_valid(self) -> None:
        self.assertEqual(1.5, _to_float(1.5))
        self.assertEqual(2.0, _to_float(2))
        self.assertEqual(3.14, _to_float("3.14"))

    def test_to_float_invalid(self) -> None:
        self.assertIsNone(_to_float(None))
        self.assertIsNone(_to_float("abc"))
        self.assertIsNone(_to_float([]))

    def test_to_int_valid(self) -> None:
        self.assertEqual(5, _to_int(5))
        self.assertEqual(10, _to_int("10"))
        self.assertEqual(3, _to_int(3.9))

    def test_to_int_invalid(self) -> None:
        self.assertIsNone(_to_int(None))
        self.assertIsNone(_to_int("xyz"))
        self.assertIsNone(_to_int([]))

    def test_to_bool_valid(self) -> None:
        self.assertIs(True, _to_bool(True))
        self.assertIs(False, _to_bool(False))

    def test_to_bool_invalid(self) -> None:
        self.assertIsNone(_to_bool(1))
        self.assertIsNone(_to_bool("true"))
        self.assertIsNone(_to_bool(None))
        self.assertIsNone(_to_bool(0))


class IsSmokeBatchSummaryTests(unittest.TestCase):
    def test_valid_ok_payload(self) -> None:
        self.assertTrue(
            _is_smoke_batch_summary(
                {"code": "SMOKE_BATCH_OK", "data": {"cases": []}}
            )
        )

    def test_valid_failed_payload(self) -> None:
        self.assertTrue(
            _is_smoke_batch_summary(
                {"code": "SMOKE_BATCH_FAILED", "data": {"cases": [{"name": "avatar"}]}}
            )
        )

    def test_wrong_code(self) -> None:
        self.assertFalse(
            _is_smoke_batch_summary(
                {"code": "OTHER_CODE", "data": {"cases": []}}
            )
        )

    def test_missing_data(self) -> None:
        self.assertFalse(_is_smoke_batch_summary({"code": "SMOKE_BATCH_OK"}))

    def test_data_not_dict(self) -> None:
        self.assertFalse(
            _is_smoke_batch_summary(
                {"code": "SMOKE_BATCH_OK", "data": "not a dict"}
            )
        )

    def test_cases_not_list(self) -> None:
        self.assertFalse(
            _is_smoke_batch_summary(
                {"code": "SMOKE_BATCH_OK", "data": {"cases": "not a list"}}
            )
        )

    def test_not_dict(self) -> None:
        self.assertFalse(_is_smoke_batch_summary("string"))
        self.assertFalse(_is_smoke_batch_summary(42))
        self.assertFalse(_is_smoke_batch_summary(None))


class CaseToRowTests(unittest.TestCase):
    def test_all_fields_present(self) -> None:
        source = Path("/tmp/test.json")
        payload: dict = {"success": True, "severity": "info"}
        case: dict = {
            "name": "avatar",
            "matched_expectation": True,
            "expected_code": "BRIDGE_OK",
            "actual_code": "BRIDGE_OK",
            "code_matches": True,
            "expected_applied": 3,
            "expected_applied_source": "plan",
            "actual_applied": 3,
            "applied_matches": True,
            "attempts": 1,
            "duration_sec": 12.5,
            "unity_timeout_sec": 300,
            "exit_code": 0,
            "response_code": "OK",
            "response_severity": "info",
            "response_path": "/tmp/resp.json",
            "unity_log_file": "/tmp/log.txt",
            "plan": "/tmp/plan.json",
            "project_path": "/tmp/project",
        }
        row = _case_to_row(source, payload, case)
        self.assertEqual("avatar", row["target"])
        self.assertTrue(row["matched_expectation"])
        self.assertEqual("BRIDGE_OK", row["expected_code"])
        self.assertEqual(True, row["code_matches"])
        self.assertEqual(3, row["expected_applied"])
        self.assertEqual(12.5, row["duration_sec"])
        self.assertEqual(300, row["unity_timeout_sec"])

    def test_missing_fields(self) -> None:
        row = _case_to_row(Path("/tmp/x.json"), {}, {})
        self.assertEqual("", row["target"])
        self.assertFalse(row["matched_expectation"])
        self.assertEqual("", row["expected_code"])
        self.assertIsNone(row["code_matches"])
        self.assertIsNone(row["duration_sec"])

    def test_non_string_code_values(self) -> None:
        case: dict = {"expected_code": 123, "actual_code": None}
        row = _case_to_row(Path("/tmp/x.json"), {}, case)
        self.assertEqual("", row["expected_code"])
        self.assertEqual("", row["actual_code"])


class PercentileTests(unittest.TestCase):
    def test_empty_list(self) -> None:
        self.assertIsNone(_percentile([], 50))

    def test_single_value(self) -> None:
        self.assertEqual(10.0, _percentile([10.0], 0))
        self.assertEqual(10.0, _percentile([10.0], 50))
        self.assertEqual(10.0, _percentile([10.0], 100))

    def test_two_values_p50(self) -> None:
        result = _percentile([10.0, 20.0], 50)
        self.assertAlmostEqual(15.0, result)  # type: ignore[arg-type]

    def test_percentile_zero_returns_min(self) -> None:
        self.assertEqual(5.0, _percentile([5.0, 10.0, 20.0], 0))

    def test_percentile_100_returns_max(self) -> None:
        self.assertEqual(20.0, _percentile([5.0, 10.0, 20.0], 100))

    def test_percentile_negative_returns_min(self) -> None:
        self.assertEqual(5.0, _percentile([5.0, 10.0, 20.0], -10))

    def test_percentile_over_100_returns_max(self) -> None:
        self.assertEqual(20.0, _percentile([5.0, 10.0, 20.0], 150))

    def test_p90_interpolation(self) -> None:
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = _percentile(values, 90)
        # position = 0.9 * 4 = 3.6; lower=40, upper=50; 40 + (50-40)*0.6 = 46
        self.assertAlmostEqual(46.0, result)  # type: ignore[arg-type]

    def test_unsorted_input(self) -> None:
        result = _percentile([30.0, 10.0, 20.0], 50)
        self.assertAlmostEqual(20.0, result)  # type: ignore[arg-type]


class RoundUpTimeoutTests(unittest.TestCase):
    def test_exact_multiple(self) -> None:
        self.assertEqual(60, _round_up_timeout(60.0, 30))

    def test_fractional(self) -> None:
        self.assertEqual(60, _round_up_timeout(50.0, 30))

    def test_small_value(self) -> None:
        self.assertEqual(30, _round_up_timeout(1.0, 30))

    def test_zero_value(self) -> None:
        self.assertEqual(0, _round_up_timeout(0.0, 30))

    def test_invalid_step_raises(self) -> None:
        with self.assertRaises(ValueError):
            _round_up_timeout(10.0, 0)
        with self.assertRaises(ValueError):
            _round_up_timeout(10.0, -1)


class BuildTargetStatsTests(unittest.TestCase):
    def _make_row(
        self,
        target: str = "avatar",
        duration: float | None = 10.0,
        timeout: int | None = 300,
        code_matches: bool | None = True,
        applied_matches: bool | None = True,
        matched_expectation: bool = True,
        attempts: int | None = 1,
    ) -> dict:
        return {
            "target": target,
            "duration_sec": duration,
            "unity_timeout_sec": timeout,
            "code_matches": code_matches,
            "applied_matches": applied_matches,
            "matched_expectation": matched_expectation,
            "attempts": attempts,
        }

    def test_single_target_single_row(self) -> None:
        rows = [self._make_row(duration=10.0, timeout=300)]
        stats = _build_target_stats(rows, 90.0)
        self.assertEqual(1, len(stats))
        self.assertEqual("avatar", stats[0]["target"])
        self.assertEqual(1, stats[0]["runs"])
        self.assertEqual(0, stats[0]["failures"])
        self.assertEqual(1, stats[0]["code_assertion_runs"])
        self.assertEqual(0, stats[0]["code_assertion_mismatches"])
        self.assertEqual(100.0, stats[0]["code_assertion_pass_pct"])

    def test_multiple_targets(self) -> None:
        rows = [
            self._make_row(target="avatar", duration=10.0),
            self._make_row(target="world", duration=20.0),
        ]
        stats = _build_target_stats(rows, 90.0)
        self.assertEqual(2, len(stats))
        targets = [s["target"] for s in stats]
        self.assertEqual(["avatar", "world"], targets)

    def test_code_assertion_mismatches(self) -> None:
        rows = [
            self._make_row(code_matches=True),
            self._make_row(code_matches=False),
            self._make_row(code_matches=True),
        ]
        stats = _build_target_stats(rows, 90.0)
        self.assertEqual(3, stats[0]["code_assertion_runs"])
        self.assertEqual(1, stats[0]["code_assertion_mismatches"])
        self.assertAlmostEqual(66.67, stats[0]["code_assertion_pass_pct"])

    def test_timeout_breach_detection(self) -> None:
        rows = [
            self._make_row(duration=100.0, timeout=200),
            self._make_row(duration=250.0, timeout=200),
        ]
        stats = _build_target_stats(rows, 90.0)
        self.assertEqual(2, stats[0]["observed_timeout_sample_count"])
        self.assertEqual(1, stats[0]["observed_timeout_breach_count"])
        self.assertAlmostEqual(50.0, stats[0]["observed_timeout_coverage_pct"])

    def test_no_duration_data(self) -> None:
        rows = [self._make_row(duration=None, timeout=None)]
        stats = _build_target_stats(rows, 90.0)
        self.assertIsNone(stats[0]["duration_avg_sec"])
        self.assertIsNone(stats[0]["duration_p_sec"])
        self.assertIsNone(stats[0]["duration_max_sec"])

    def test_no_code_matches_data(self) -> None:
        rows = [self._make_row(code_matches=None)]
        stats = _build_target_stats(rows, 90.0)
        self.assertEqual(0, stats[0]["code_assertion_runs"])
        self.assertIsNone(stats[0]["code_assertion_pass_pct"])

    def test_failure_count(self) -> None:
        rows = [
            self._make_row(matched_expectation=True),
            self._make_row(matched_expectation=False),
            self._make_row(matched_expectation=False),
        ]
        stats = _build_target_stats(rows, 90.0)
        self.assertEqual(2, stats[0]["failures"])


class BuildTimeoutProfilesTests(unittest.TestCase):
    _DEFAULT_DURATION_VALUES = [80.0, 90.0, 100.0, 110.0, 120.0]

    def _make_stats(
        self,
        target: str = "avatar",
        runs: int = 5,
        failures: int = 0,
        duration_p_sec: float | None = 100.0,
        duration_max_sec: float | None = 120.0,
        timeout_max_sec: int | None = 300,
        duration_values: list[float] | None = _DEFAULT_DURATION_VALUES,
    ) -> dict:
        return {
            "target": target,
            "runs": runs,
            "failures": failures,
            "duration_p_sec": duration_p_sec,
            "duration_max_sec": duration_max_sec,
            "timeout_max_sec": timeout_max_sec,
            "duration_values_sec": duration_values if duration_values is not None else [],
        }

    def test_basic_profile(self) -> None:
        stats = [self._make_stats()]
        result = _build_timeout_profiles(
            stats,
            duration_percentile=90.0,
            timeout_multiplier=1.5,
            timeout_slack_sec=60,
            timeout_min_sec=300,
            timeout_round_sec=30,
        )
        self.assertEqual(1, result["version"])
        self.assertEqual(1, len(result["profiles"]))
        profile = result["profiles"][0]
        self.assertEqual("avatar", profile["target"])
        # Candidates: min=300, p*mult=100*1.5=150, max+slack=120+60=180, observed=300
        # base = max(300, 150, 180, 300) = 300, no failures, round_up(300, 30) = 300
        self.assertEqual(300, profile["recommended_timeout_sec"])

    def test_failure_adds_slack(self) -> None:
        stats = [self._make_stats(failures=1)]
        result = _build_timeout_profiles(
            stats,
            duration_percentile=90.0,
            timeout_multiplier=1.5,
            timeout_slack_sec=60,
            timeout_min_sec=300,
            timeout_round_sec=30,
        )
        profile = result["profiles"][0]
        # base = 300, + failure slack 60 = 360, round_up(360, 30) = 360
        self.assertEqual(360, profile["recommended_timeout_sec"])

    def test_high_duration_drives_recommendation(self) -> None:
        stats = [self._make_stats(duration_max_sec=500.0, timeout_max_sec=200)]
        result = _build_timeout_profiles(
            stats,
            duration_percentile=90.0,
            timeout_multiplier=1.5,
            timeout_slack_sec=60,
            timeout_min_sec=300,
            timeout_round_sec=30,
        )
        profile = result["profiles"][0]
        # Candidates: min=300, p*mult=150, max+slack=500+60=560, observed=200
        # base = 560, round_up(560, 30) = 570
        self.assertEqual(570, profile["recommended_timeout_sec"])

    def test_breach_count_in_evidence(self) -> None:
        stats = [self._make_stats(duration_values=[100.0, 200.0, 350.0])]
        result = _build_timeout_profiles(
            stats,
            duration_percentile=90.0,
            timeout_multiplier=1.5,
            timeout_slack_sec=60,
            timeout_min_sec=300,
            timeout_round_sec=30,
        )
        evidence = result["profiles"][0]["evidence"]
        self.assertEqual(3, evidence["duration_sample_count"])
        # recommended is 300; 350 > 300 → 1 breach
        self.assertEqual(1, evidence["timeout_breach_count"])

    def test_cli_arg_format(self) -> None:
        stats = [self._make_stats()]
        result = _build_timeout_profiles(
            stats,
            duration_percentile=90.0,
            timeout_multiplier=1.5,
            timeout_slack_sec=60,
            timeout_min_sec=300,
            timeout_round_sec=30,
        )
        self.assertEqual(
            "--avatar-unity-timeout-sec 300",
            result["profiles"][0]["recommended_cli_arg"],
        )

    def test_empty_duration_values(self) -> None:
        stats = [
            self._make_stats(
                duration_p_sec=None,
                duration_max_sec=None,
                timeout_max_sec=None,
                duration_values=None,
            )
        ]
        result = _build_timeout_profiles(
            stats,
            duration_percentile=90.0,
            timeout_multiplier=1.5,
            timeout_slack_sec=60,
            timeout_min_sec=300,
            timeout_round_sec=30,
        )
        profile = result["profiles"][0]
        # Only candidate is min_timeout=300
        self.assertEqual(300, profile["recommended_timeout_sec"])
        # No duration samples → 0 breaches / 0 samples, but coverage is computed as 100%
        # since there are no breaches possible
        self.assertEqual(0, profile["evidence"]["duration_sample_count"])


class ComputeCodeAssertionMetricsTests(unittest.TestCase):
    def test_empty_rows(self) -> None:
        assertions, mismatches, pct = _compute_code_assertion_metrics([])
        self.assertEqual(0, assertions)
        self.assertEqual(0, mismatches)
        self.assertIsNone(pct)

    def test_all_match(self) -> None:
        rows = [{"code_matches": True}, {"code_matches": True}]
        assertions, mismatches, pct = _compute_code_assertion_metrics(rows)
        self.assertEqual(2, assertions)
        self.assertEqual(0, mismatches)
        self.assertAlmostEqual(100.0, pct)

    def test_all_mismatch(self) -> None:
        rows = [{"code_matches": False}, {"code_matches": False}]
        assertions, mismatches, pct = _compute_code_assertion_metrics(rows)
        self.assertEqual(2, assertions)
        self.assertEqual(2, mismatches)
        self.assertAlmostEqual(0.0, pct)

    def test_mixed(self) -> None:
        rows = [{"code_matches": True}, {"code_matches": False}, {"code_matches": True}]
        assertions, mismatches, pct = _compute_code_assertion_metrics(rows)
        self.assertEqual(3, assertions)
        self.assertEqual(1, mismatches)
        self.assertAlmostEqual(66.667, pct, places=2)

    def test_non_bool_values_ignored(self) -> None:
        rows = [{"code_matches": True}, {"code_matches": "yes"}, {"code_matches": None}]
        assertions, mismatches, pct = _compute_code_assertion_metrics(rows)
        self.assertEqual(1, assertions)


class ComputeAppliedAssertionMetricsTests(unittest.TestCase):
    def test_empty_rows(self) -> None:
        assertions, mismatches, pct = _compute_applied_assertion_metrics([])
        self.assertEqual(0, assertions)
        self.assertIsNone(pct)

    def test_all_match(self) -> None:
        rows = [{"applied_matches": True}, {"applied_matches": True}]
        assertions, mismatches, pct = _compute_applied_assertion_metrics(rows)
        self.assertEqual(2, assertions)
        self.assertEqual(0, mismatches)
        self.assertAlmostEqual(100.0, pct)


class ComputeObservedTimeoutMetricsTests(unittest.TestCase):
    def test_empty_stats(self) -> None:
        samples, breaches, pct = _compute_observed_timeout_metrics([])
        self.assertEqual(0, samples)
        self.assertEqual(0, breaches)
        self.assertIsNone(pct)

    def test_single_target_no_breach(self) -> None:
        stats = [
            {
                "target": "avatar",
                "observed_timeout_sample_count": 5,
                "observed_timeout_breach_count": 0,
            }
        ]
        samples, breaches, pct = _compute_observed_timeout_metrics(stats)
        self.assertEqual(5, samples)
        self.assertEqual(0, breaches)
        self.assertAlmostEqual(100.0, pct)

    def test_single_target_with_breach(self) -> None:
        stats = [
            {
                "target": "avatar",
                "observed_timeout_sample_count": 10,
                "observed_timeout_breach_count": 2,
            }
        ]
        samples, breaches, pct = _compute_observed_timeout_metrics(stats)
        self.assertEqual(10, samples)
        self.assertEqual(2, breaches)
        self.assertAlmostEqual(80.0, pct)

    def test_multiple_targets_aggregation(self) -> None:
        stats = [
            {
                "target": "avatar",
                "observed_timeout_sample_count": 5,
                "observed_timeout_breach_count": 1,
            },
            {
                "target": "world",
                "observed_timeout_sample_count": 5,
                "observed_timeout_breach_count": 0,
            },
        ]
        samples, breaches, pct = _compute_observed_timeout_metrics(stats)
        self.assertEqual(10, samples)
        self.assertEqual(1, breaches)
        self.assertAlmostEqual(90.0, pct)


class ComputeObservedTimeoutMetricsByTargetTests(unittest.TestCase):
    def test_empty(self) -> None:
        result = _compute_observed_timeout_metrics_by_target([])
        self.assertEqual({}, result)

    def test_single_target(self) -> None:
        stats = [
            {
                "target": "avatar",
                "observed_timeout_sample_count": 10,
                "observed_timeout_breach_count": 1,
            }
        ]
        result = _compute_observed_timeout_metrics_by_target(stats)
        self.assertIn("avatar", result)
        self.assertAlmostEqual(90.0, result["avatar"]["observed_timeout_coverage_pct"])

    def test_zero_samples(self) -> None:
        stats = [
            {
                "target": "avatar",
                "observed_timeout_sample_count": 0,
                "observed_timeout_breach_count": 0,
            }
        ]
        result = _compute_observed_timeout_metrics_by_target(stats)
        self.assertIsNone(result["avatar"]["observed_timeout_coverage_pct"])


class ComputeProfileTimeoutMetricsTests(unittest.TestCase):
    def test_none_payload(self) -> None:
        samples, breaches, pct = _compute_profile_timeout_metrics(None)
        self.assertEqual(0, samples)
        self.assertEqual(0, breaches)
        self.assertIsNone(pct)

    def test_valid_profile(self) -> None:
        payload = {
            "profiles": [
                {
                    "target": "avatar",
                    "evidence": {
                        "duration_sample_count": 10,
                        "timeout_breach_count": 1,
                    },
                }
            ]
        }
        samples, breaches, pct = _compute_profile_timeout_metrics(payload)
        self.assertEqual(10, samples)
        self.assertEqual(1, breaches)
        self.assertAlmostEqual(90.0, pct)


class ComputeProfileTimeoutMetricsByTargetTests(unittest.TestCase):
    def test_none_payload(self) -> None:
        self.assertEqual({}, _compute_profile_timeout_metrics_by_target(None))

    def test_invalid_profiles_field(self) -> None:
        self.assertEqual({}, _compute_profile_timeout_metrics_by_target({"profiles": "bad"}))

    def test_valid(self) -> None:
        payload = {
            "profiles": [
                {
                    "target": "avatar",
                    "evidence": {
                        "duration_sample_count": 5,
                        "timeout_breach_count": 0,
                    },
                },
                {
                    "target": "world",
                    "evidence": {
                        "duration_sample_count": 5,
                        "timeout_breach_count": 2,
                    },
                },
            ]
        }
        by_target = _compute_profile_timeout_metrics_by_target(payload)
        self.assertEqual(2, len(by_target))
        self.assertAlmostEqual(100.0, by_target["avatar"]["timeout_coverage_pct"])
        self.assertAlmostEqual(60.0, by_target["world"]["timeout_coverage_pct"])

    def test_non_dict_profile_skipped(self) -> None:
        payload = {
            "profiles": [
                "not_a_dict",
                {
                    "target": "avatar",
                    "evidence": {"duration_sample_count": 1, "timeout_breach_count": 0},
                },
            ]
        }
        by_target = _compute_profile_timeout_metrics_by_target(payload)
        self.assertEqual(1, len(by_target))
        self.assertIn("avatar", by_target)


class ExpandInputsTests(unittest.TestCase):
    def test_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "summary.json"
            f.write_text("{}", encoding="utf-8")
            result = _expand_inputs([str(f)])
            self.assertEqual(1, len(result))
            self.assertEqual(f.resolve(), result[0])

    def test_glob_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.json").write_text("{}", encoding="utf-8")
            (root / "b.json").write_text("{}", encoding="utf-8")
            (root / "c.txt").write_text("", encoding="utf-8")
            result = _expand_inputs([str(root / "*.json")])
            self.assertEqual(2, len(result))

    def test_nonexistent_returns_empty(self) -> None:
        result = _expand_inputs(["/nonexistent/path/summary.json"])
        self.assertEqual([], result)

    def test_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "summary.json"
            f.write_text("{}", encoding="utf-8")
            result = _expand_inputs([str(f), str(f)])
            self.assertEqual(1, len(result))


class RenderMarkdownSummaryTests(unittest.TestCase):
    def test_header_and_table(self) -> None:
        rows = [
            {
                "target": "avatar",
                "code_matches": True,
                "applied_matches": True,
                "duration_sec": 10.0,
                "unity_timeout_sec": 300,
                "matched_expectation": True,
                "attempts": 1,
            }
        ]
        md = _render_markdown_summary(rows, 90.0)
        self.assertIn("# Bridge Smoke Timeout Decision Table", md)
        self.assertIn("Cases: 1", md)
        self.assertIn("duration_p90_sec", md)
        self.assertIn("| avatar |", md)

    def test_empty_profile_renders_na(self) -> None:
        rows = [{"target": "avatar", "code_matches": True, "applied_matches": True}]
        md = _render_markdown_summary(rows, 90.0, profile_payload=None)
        self.assertIn("Profile timeout coverage pct: n/a", md)


class RunFromArgsIntegrationTests(unittest.TestCase):
    """Integration tests for run_from_args via the public main() entry point."""

    def _make_summary(self, target: str = "avatar", code_matches: bool = True) -> dict:
        return {
            "success": True,
            "severity": "info",
            "code": "SMOKE_BATCH_OK",
            "message": "ok",
            "data": {
                "cases": [
                    {
                        "name": target,
                        "matched_expectation": True,
                        "expected_code": "BRIDGE_OK",
                        "actual_code": "BRIDGE_OK",
                        "code_matches": code_matches,
                        "expected_applied": 1,
                        "actual_applied": 1,
                        "applied_matches": True,
                        "attempts": 1,
                        "duration_sec": 10.0,
                        "unity_timeout_sec": 300,
                        "exit_code": 0,
                    }
                ]
            },
            "diagnostics": [],
        }

    def test_csv_output_written(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            summary = root / "summary.json"
            summary.write_text(json.dumps(self._make_summary()), encoding="utf-8")
            out_csv = root / "out.csv"
            exit_code = smoke_history_main(["--inputs", str(summary), "--out", str(out_csv)])
            self.assertEqual(0, exit_code)
            self.assertTrue(out_csv.exists())
            content = out_csv.read_text(encoding="utf-8")
            self.assertIn("avatar", content)

    def test_markdown_output_written(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            summary = root / "summary.json"
            summary.write_text(json.dumps(self._make_summary()), encoding="utf-8")
            out_csv = root / "out.csv"
            out_md = root / "out.md"
            exit_code = smoke_history_main([
                "--inputs", str(summary),
                "--out", str(out_csv),
                "--out-md", str(out_md),
            ])
            self.assertEqual(0, exit_code)
            self.assertTrue(out_md.exists())

    def test_timeout_profile_output_written(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            summary = root / "summary.json"
            summary.write_text(json.dumps(self._make_summary()), encoding="utf-8")
            out_csv = root / "out.csv"
            out_profile = root / "profile.json"
            exit_code = smoke_history_main([
                "--inputs", str(summary),
                "--out", str(out_csv),
                "--out-timeout-profile", str(out_profile),
            ])
            self.assertEqual(0, exit_code)
            self.assertTrue(out_profile.exists())
            payload = json.loads(out_profile.read_text(encoding="utf-8"))
            self.assertIn("profiles", payload)
            self.assertEqual(1, len(payload["profiles"]))

    def test_code_mismatch_threshold_violation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            summary = root / "summary.json"
            summary.write_text(
                json.dumps(self._make_summary(code_matches=False)), encoding="utf-8"
            )
            out_csv = root / "out.csv"
            exit_code = smoke_history_main([
                "--inputs", str(summary),
                "--out", str(out_csv),
                "--max-code-mismatches", "0",
            ])
            self.assertEqual(1, exit_code)

    def test_target_filter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            summary_data = {
                "success": True,
                "severity": "info",
                "code": "SMOKE_BATCH_OK",
                "message": "ok",
                "data": {
                    "cases": [
                        {
                            "name": "avatar",
                            "matched_expectation": True,
                            "code_matches": True,
                            "duration_sec": 10.0,
                            "unity_timeout_sec": 300,
                        },
                        {
                            "name": "world",
                            "matched_expectation": True,
                            "code_matches": True,
                            "duration_sec": 20.0,
                            "unity_timeout_sec": 300,
                        },
                    ]
                },
                "diagnostics": [],
            }
            summary = root / "summary.json"
            summary.write_text(json.dumps(summary_data), encoding="utf-8")
            out_csv = root / "out.csv"
            exit_code = smoke_history_main([
                "--inputs", str(summary),
                "--out", str(out_csv),
                "--target", "avatar",
            ])
            self.assertEqual(0, exit_code)
            content = out_csv.read_text(encoding="utf-8")
            self.assertIn("avatar", content)
            self.assertNotIn("world", content)


if __name__ == "__main__":
    unittest.main()
