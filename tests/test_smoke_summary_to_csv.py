from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.smoke_summary_to_csv import (
    _build_timeout_profiles,
    _build_target_stats,
    _expand_inputs,
    _is_smoke_batch_summary,
    _percentile,
    _render_markdown_summary,
    main,
)


def _summary_payload(cases: list[dict[str, object]], *, success: bool = True) -> dict[str, object]:
    return {
        "success": success,
        "severity": "info" if success else "error",
        "code": "SMOKE_BATCH_OK" if success else "SMOKE_BATCH_FAILED",
        "message": "ok" if success else "failed",
        "data": {
            "total_cases": len(cases),
            "passed_cases": len([case for case in cases if case.get("matched_expectation")]),
            "failed_cases": len([case for case in cases if not case.get("matched_expectation")]),
            "cases": cases,
        },
        "diagnostics": [],
    }


class SmokeSummaryToCsvTests(unittest.TestCase):
    def test_expand_inputs_supports_glob_and_slash_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "a" / "summary.json"
            second = root / "b" / "summary.json"
            first.parent.mkdir(parents=True, exist_ok=True)
            second.parent.mkdir(parents=True, exist_ok=True)
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")

            slash_pattern = str(root / "*" / "*.json").replace("\\", "/")
            backslash_pattern = slash_pattern.replace("/", "\\")
            expanded = _expand_inputs([slash_pattern, backslash_pattern])

        self.assertEqual(2, len(expanded))
        self.assertTrue(any(path.name == "summary.json" for path in expanded))

    def test_is_smoke_batch_summary_rejects_invalid(self) -> None:
        self.assertFalse(_is_smoke_batch_summary({}))
        self.assertFalse(_is_smoke_batch_summary({"code": "SMOKE_BATCH_OK", "data": {}}))
        self.assertFalse(_is_smoke_batch_summary({"code": "OTHER", "data": {"cases": []}}))
        self.assertTrue(_is_smoke_batch_summary({"code": "SMOKE_BATCH_OK", "data": {"cases": []}}))

    def test_percentile_uses_interpolation(self) -> None:
        self.assertAlmostEqual(3.7, _percentile([1.0, 2.0, 3.0, 4.0], 90.0) or 0.0)

    def test_build_target_stats_aggregates_values(self) -> None:
        rows = [
            {
                "target": "avatar",
                "matched_expectation": True,
                "code_matches": True,
                "applied_matches": True,
                "attempts": 1,
                "duration_sec": 1.0,
                "unity_timeout_sec": 600,
            },
            {
                "target": "avatar",
                "matched_expectation": False,
                "code_matches": False,
                "applied_matches": False,
                "attempts": 2,
                "duration_sec": 3.0,
                "unity_timeout_sec": 900,
            },
        ]
        stats = _build_target_stats(rows, 90.0)
        self.assertEqual(1, len(stats))
        self.assertEqual("avatar", stats[0]["target"])
        self.assertEqual(2, stats[0]["runs"])
        self.assertEqual(1, stats[0]["failures"])
        self.assertEqual(2, stats[0]["code_assertion_runs"])
        self.assertEqual(1, stats[0]["code_assertion_mismatches"])
        self.assertAlmostEqual(50.0, stats[0]["code_assertion_pass_pct"] or 0.0)
        self.assertEqual(2, stats[0]["applied_assertion_runs"])
        self.assertEqual(1, stats[0]["applied_assertion_mismatches"])
        self.assertAlmostEqual(50.0, stats[0]["applied_assertion_pass_pct"] or 0.0)
        self.assertEqual(2, stats[0]["observed_timeout_sample_count"])
        self.assertEqual(0, stats[0]["observed_timeout_breach_count"])
        self.assertAlmostEqual(100.0, stats[0]["observed_timeout_coverage_pct"] or 0.0)
        self.assertEqual(2, stats[0]["attempts_max"])
        self.assertAlmostEqual(2.8, stats[0]["duration_p_sec"] or 0.0)
        self.assertEqual(900, stats[0]["timeout_max_sec"])
        self.assertEqual([1.0, 3.0], stats[0]["duration_values_sec"])


    def test_build_timeout_profiles_generates_recommended_values(self) -> None:
        stats = [
            {
                "target": "avatar",
                "runs": 3,
                "failures": 1,
                "duration_p_sec": 100.0,
                "duration_max_sec": 140.0,
                "timeout_max_sec": 180,
            },
            {
                "target": "world",
                "runs": 2,
                "failures": 0,
                "duration_p_sec": 30.0,
                "duration_max_sec": 40.0,
                "timeout_max_sec": None,
            },
        ]

        payload = _build_timeout_profiles(
            stats,
            duration_percentile=90.0,
            timeout_multiplier=1.5,
            timeout_slack_sec=60,
            timeout_min_sec=120,
            timeout_round_sec=30,
        )

        self.assertEqual(1, payload["version"])
        self.assertEqual(2, len(payload["profiles"]))
        avatar_profile = payload["profiles"][0]
        world_profile = payload["profiles"][1]

        self.assertEqual("avatar", avatar_profile["target"])
        self.assertEqual(270, avatar_profile["recommended_timeout_sec"])
        self.assertEqual(
            "--avatar-unity-timeout-sec 270", avatar_profile["recommended_cli_arg"]
        )
        self.assertEqual("world", world_profile["target"])
        self.assertEqual(120, world_profile["recommended_timeout_sec"])

    def test_build_timeout_profiles_includes_coverage_metrics(self) -> None:
        stats = [
            {
                "target": "avatar",
                "runs": 2,
                "failures": 0,
                "duration_p_sec": 10.0,
                "duration_max_sec": 20.0,
                "timeout_max_sec": None,
                "duration_values_sec": [5.0, 55.0],
            }
        ]

        payload = _build_timeout_profiles(
            stats,
            duration_percentile=90.0,
            timeout_multiplier=1.5,
            timeout_slack_sec=0,
            timeout_min_sec=30,
            timeout_round_sec=10,
        )

        profile = payload["profiles"][0]
        evidence = profile["evidence"]
        self.assertEqual(30, profile["recommended_timeout_sec"])
        self.assertEqual(2, evidence["duration_sample_count"])
        self.assertEqual(1, evidence["timeout_breach_count"])
        self.assertAlmostEqual(50.0, evidence["timeout_coverage_pct"] or 0.0)

    def test_render_markdown_summary_includes_target_rows(self) -> None:
        rows = [
            {
                "target": "avatar",
                "matched_expectation": True,
                "code_matches": True,
                "attempts": 1,
                "duration_sec": 1.2,
                "unity_timeout_sec": 600,
            },
            {
                "target": "world",
                "matched_expectation": True,
                "code_matches": True,
                "attempts": 1,
                "duration_sec": 2.4,
                "unity_timeout_sec": 700,
            },
        ]
        markdown = _render_markdown_summary(rows, 90.0)
        self.assertIn("# Bridge Smoke Timeout Decision Table", markdown)
        self.assertIn("- Code assertion runs: 2", markdown)
        self.assertIn("- Profile timeout samples: 0", markdown)
        self.assertIn("| target | runs | failures | code_assertions |", markdown)
        self.assertIn("| avatar |", markdown)
        self.assertIn("| world |", markdown)

    def test_main_writes_csv_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_a = root / "summary_a.json"
            summary_b = root / "summary_b.json"
            out_csv = root / "smoke_history.csv"
            out_md = root / "smoke_history.md"
            out_profile = root / "timeout_profile.json"
            summary_a.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "expected_code": "OK",
                                "actual_code": "OK",
                                "code_matches": True,
                                "expected_applied": 1,
                                "expected_applied_source": "cli",
                                "actual_applied": 1,
                                "applied_matches": True,
                                "attempts": 1,
                                "duration_sec": 1.1,
                                "unity_timeout_sec": 600,
                                "exit_code": 0,
                                "response_code": "OK",
                                "response_severity": "info",
                                "response_path": "reports/avatar/response.json",
                                "unity_log_file": "reports/avatar/unity.log",
                                "plan": "sample/avatar/config/prefab_patch_plan.json",
                                "project_path": "sample/avatar",
                            }
                        ]
                    )
                ),
                encoding="utf-8",
            )
            summary_b.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "world",
                                "matched_expectation": False,
                                "expected_code": "OK",
                                "actual_code": "SMOKE_BRIDGE_ERROR",
                                "code_matches": False,
                                "expected_applied": 2,
                                "expected_applied_source": "plan_ops",
                                "actual_applied": 1,
                                "applied_matches": False,
                                "attempts": 2,
                                "duration_sec": 2.9,
                                "unity_timeout_sec": 900,
                                "exit_code": 1,
                                "response_code": "SMOKE_BRIDGE_ERROR",
                                "response_severity": "error",
                                "response_path": "reports/world/response.json",
                                "unity_log_file": "reports/world/unity.log",
                                "plan": "sample/world/config/prefab_patch_plan.json",
                                "project_path": "sample/world",
                            }
                        ],
                        success=False,
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(root / "summary_*.json"),
                        "--out",
                        str(out_csv),
                        "--out-md",
                        str(out_md),
                        "--out-timeout-profile",
                        str(out_profile),
                        "--timeout-multiplier",
                        "1.2",
                        "--timeout-slack-sec",
                        "30",
                        "--timeout-min-sec",
                        "120",
                        "--timeout-round-sec",
                        "10",
                    ]
                )

            csv_text = out_csv.read_text(encoding="utf-8")
            md_text = out_md.read_text(encoding="utf-8")
            profile = json.loads(out_profile.read_text(encoding="utf-8"))

        self.assertEqual(0, exit_code)
        self.assertIn("expected_code", csv_text)
        self.assertIn("actual_code", csv_text)
        self.assertIn("code_matches", csv_text)
        self.assertIn("expected_applied_source", csv_text)
        self.assertIn(",OK,OK,True,", csv_text)
        self.assertIn(",OK,SMOKE_BRIDGE_ERROR,False,", csv_text)
        self.assertIn(",plan_ops,", csv_text)
        self.assertIn("avatar", csv_text)
        self.assertIn("world", csv_text)
        self.assertIn("applied_mismatches", md_text)
        self.assertIn("Applied assertion runs", md_text)
        self.assertIn("Observed timeout targets", md_text)
        self.assertIn("Observed timeout breaches", md_text)
        self.assertIn("Profile timeout targets", md_text)
        self.assertIn("profile_timeout_samples", md_text)
        self.assertIn("| avatar |", md_text)
        self.assertIn("| world |", md_text)
        self.assertEqual(2, len(profile["profiles"]))
        self.assertEqual("avatar", profile["profiles"][0]["target"])
        self.assertEqual("world", profile["profiles"][1]["target"])
        self.assertEqual(
            "--avatar-unity-timeout-sec 600",
            profile["profiles"][0]["recommended_cli_arg"],
        )
        self.assertEqual(
            "--world-unity-timeout-sec 930",
            profile["profiles"][1]["recommended_cli_arg"],
        )

    def test_main_filters_target_and_matched_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            out_csv = root / "filtered.csv"
            summary_path.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "attempts": 1,
                                "duration_sec": 1.0,
                            },
                            {
                                "name": "avatar",
                                "matched_expectation": False,
                                "attempts": 2,
                                "duration_sec": 2.0,
                            },
                            {
                                "name": "world",
                                "matched_expectation": True,
                                "attempts": 1,
                                "duration_sec": 3.0,
                            },
                        ]
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(summary_path),
                        "--target",
                        "avatar",
                        "--matched-only",
                        "--out",
                        str(out_csv),
                    ]
                )
            csv_lines = [line for line in out_csv.read_text(encoding="utf-8").splitlines() if line]

        self.assertEqual(0, exit_code)
        self.assertEqual(2, len(csv_lines))
        self.assertIn(",avatar,", csv_lines[1])

    def test_main_fails_when_applied_mismatch_threshold_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            out_csv = root / "history.csv"
            summary_path.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "applied_matches": True,
                            },
                            {
                                "name": "avatar",
                                "matched_expectation": False,
                                "applied_matches": False,
                            },
                        ],
                        success=False,
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(summary_path),
                        "--out",
                        str(out_csv),
                        "--max-applied-mismatches",
                        "0",
                    ]
                )
            out_exists = out_csv.exists()

        self.assertEqual(1, exit_code)
        self.assertTrue(out_exists)

    def test_main_fails_when_code_mismatch_threshold_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            out_csv = root / "history.csv"
            summary_path.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "expected_code": "OK",
                                "actual_code": "OK",
                                "code_matches": True,
                            },
                            {
                                "name": "avatar",
                                "matched_expectation": False,
                                "expected_code": "OK",
                                "actual_code": "ERR",
                                "code_matches": False,
                            },
                        ],
                        success=False,
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(summary_path),
                        "--out",
                        str(out_csv),
                        "--max-code-mismatches",
                        "0",
                    ]
                )
            out_exists = out_csv.exists()

        self.assertEqual(1, exit_code)
        self.assertTrue(out_exists)

    def test_main_fails_when_applied_pass_pct_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            out_csv = root / "history.csv"
            summary_path.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "applied_matches": True,
                            },
                            {
                                "name": "avatar",
                                "matched_expectation": False,
                                "applied_matches": False,
                            },
                        ],
                        success=False,
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(summary_path),
                        "--out",
                        str(out_csv),
                        "--min-applied-pass-pct",
                        "80",
                    ]
                )
            out_exists = out_csv.exists()

        self.assertEqual(1, exit_code)
        self.assertTrue(out_exists)

    def test_main_fails_when_code_pass_pct_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            out_csv = root / "history.csv"
            summary_path.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "expected_code": "OK",
                                "actual_code": "OK",
                                "code_matches": True,
                            },
                            {
                                "name": "avatar",
                                "matched_expectation": False,
                                "expected_code": "OK",
                                "actual_code": "ERR",
                                "code_matches": False,
                            },
                        ],
                        success=False,
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(summary_path),
                        "--out",
                        str(out_csv),
                        "--min-code-pass-pct",
                        "80",
                    ]
                )
            out_exists = out_csv.exists()

        self.assertEqual(1, exit_code)
        self.assertTrue(out_exists)

    def test_main_fails_when_observed_timeout_breach_threshold_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            out_csv = root / "history.csv"
            summary_path.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "duration_sec": 650.0,
                                "unity_timeout_sec": 600,
                            },
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "duration_sec": 500.0,
                                "unity_timeout_sec": 600,
                            },
                        ]
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(summary_path),
                        "--out",
                        str(out_csv),
                        "--max-observed-timeout-breaches",
                        "0",
                    ]
                )
            out_exists = out_csv.exists()

        self.assertEqual(1, exit_code)
        self.assertTrue(out_exists)

    def test_main_fails_when_observed_timeout_coverage_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            out_csv = root / "history.csv"
            summary_path.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "duration_sec": 650.0,
                                "unity_timeout_sec": 600,
                            },
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "duration_sec": 500.0,
                                "unity_timeout_sec": 600,
                            },
                        ]
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(summary_path),
                        "--out",
                        str(out_csv),
                        "--min-observed-timeout-coverage-pct",
                        "80",
                    ]
                )
            out_exists = out_csv.exists()

        self.assertEqual(1, exit_code)
        self.assertTrue(out_exists)


    def test_main_fails_when_observed_timeout_coverage_per_target_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            out_csv = root / "history.csv"
            summary_path.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "duration_sec": 650.0,
                                "unity_timeout_sec": 600,
                            },
                            {
                                "name": "world",
                                "matched_expectation": True,
                                "attempts": 1,
                            },
                        ]
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(summary_path),
                        "--out",
                        str(out_csv),
                        "--min-observed-timeout-coverage-pct-per-target",
                        "100",
                    ]
                )
            out_exists = out_csv.exists()

        self.assertEqual(1, exit_code)
        self.assertTrue(out_exists)

    def test_main_fails_when_profile_timeout_coverage_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            out_csv = root / "history.csv"
            summary_path.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "attempts": 1,
                            },
                        ]
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(summary_path),
                        "--out",
                        str(out_csv),
                        "--min-profile-timeout-coverage-pct",
                        "100",
                    ]
                )
            out_exists = out_csv.exists()

        self.assertEqual(1, exit_code)
        self.assertTrue(out_exists)


    def test_main_fails_when_profile_timeout_coverage_per_target_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            out_csv = root / "history.csv"
            summary_path.write_text(
                json.dumps(
                    _summary_payload(
                        [
                            {
                                "name": "avatar",
                                "matched_expectation": True,
                                "attempts": 1,
                                "duration_sec": 120.0,
                                "unity_timeout_sec": 600,
                            },
                            {
                                "name": "world",
                                "matched_expectation": True,
                                "attempts": 1,
                            },
                        ]
                    )
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--inputs",
                        str(summary_path),
                        "--out",
                        str(out_csv),
                        "--min-profile-timeout-coverage-pct-per-target",
                        "100",
                    ]
                )
            out_exists = out_csv.exists()

        self.assertEqual(1, exit_code)
        self.assertTrue(out_exists)

    def test_main_rejects_invalid_timeout_profile_arguments(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--timeout-multiplier",
                        "0.9",
                    ]
                )

        self.assertEqual(2, raised.exception.code)

    def test_main_rejects_invalid_observed_timeout_threshold_arguments(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised_breaches:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--max-observed-timeout-breaches",
                        "-1",
                    ]
                )

        self.assertEqual(2, raised_breaches.exception.code)

        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised_breaches_per_target:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--max-observed-timeout-breaches-per-target",
                        "-1",
                    ]
                )

        self.assertEqual(2, raised_breaches_per_target.exception.code)

        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised_coverage_per_target:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--min-observed-timeout-coverage-pct-per-target",
                        "101",
                    ]
                )

        self.assertEqual(2, raised_coverage_per_target.exception.code)

    def test_main_rejects_invalid_applied_threshold_arguments(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--max-applied-mismatches",
                        "-1",
                    ]
                )

        self.assertEqual(2, raised.exception.code)

    def test_main_rejects_invalid_code_threshold_arguments(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised_mismatch:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--max-code-mismatches",
                        "-1",
                    ]
                )

        self.assertEqual(2, raised_mismatch.exception.code)

        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised_pass_pct:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--min-code-pass-pct",
                        "101",
                    ]
                )

        self.assertEqual(2, raised_pass_pct.exception.code)

    def test_main_rejects_invalid_profile_timeout_threshold_arguments(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised_breaches:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--max-profile-timeout-breaches",
                        "-1",
                    ]
                )

        self.assertEqual(2, raised_breaches.exception.code)

        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised_coverage:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--min-profile-timeout-coverage-pct",
                        "101",
                    ]
                )

        self.assertEqual(2, raised_coverage.exception.code)

        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised_breaches_per_target:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--max-profile-timeout-breaches-per-target",
                        "-1",
                    ]
                )

        self.assertEqual(2, raised_breaches_per_target.exception.code)

        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised_coverage_per_target:
                main(
                    [
                        "--inputs",
                        "missing.json",
                        "--out",
                        "out.csv",
                        "--min-profile-timeout-coverage-pct-per-target",
                        "101",
                    ]
                )

        self.assertEqual(2, raised_coverage_per_target.exception.code)


if __name__ == "__main__":
    unittest.main()
