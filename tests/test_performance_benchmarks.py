from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from prefab_sentinel.benchmarking import (
    BenchmarkCase,
    BenchmarkRunner,
    CaseMeasurement,
    aggregate_samples,
    evaluate_case_status,
)
from prefab_sentinel.benchmarking.application import (
    BUDGET_CODE,
    CONFIGURATION_CODE,
    REPORT_WRITE_CODE,
    REPORT_WRITE_MESSAGE,
    run_benchmark_application,
)
from prefab_sentinel.benchmarking.environment import (
    EnvironmentFingerprint,
    assert_same_host_fixture,
)
from prefab_sentinel.benchmarking.fixture import generate_fixture
from prefab_sentinel.benchmarking.manifest import (
    EXPECTED_CARDINALITIES,
    EXPECTED_CASE_IDS,
    BenchmarkConfigurationError,
    load_manifest,
)
from prefab_sentinel.benchmarking.report import BenchmarkReportWriteError
from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.session_cache import SessionCacheManager

_MANIFEST_PATH = Path(__file__).parents[1] / "benchmarks" / "inspection-performance.v1.json"


class TestBenchmarkSamplingAndAggregation(unittest.TestCase):
    def test_report_statistics_use_the_complete_measured_sample_set(self) -> None:
        samples = (1.0, 2.0, 3.0, 4.0, 100.0)

        statistics = aggregate_samples(samples)

        self.assertEqual(
            (3.0, 100.0, 1.0, 100.0, 1.0),
            (
                statistics.median_sec,
                statistics.p95_sec,
                statistics.minimum_sec,
                statistics.maximum_sec,
                statistics.median_absolute_deviation_sec,
            ),
            msg="statistics must retain the complete sample set and use median absolute deviation",
        )


class _RecordingSession:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    def inspect_wiring(self, **arguments: Any) -> ToolResponse:
        self.calls.append(dict(arguments))
        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="INSPECT_WIRING_RESULT",
            message="complete",
            data={"partial": False},
        )


class TestBenchmarkTrialLifecycle(unittest.TestCase):
    def test_warm_trials_use_fresh_sessions_and_exclude_warmups_from_samples(self) -> None:
        clock_values = iter((0.0, 1.0, 10.0, 12.0, 20.0, 23.0, 30.0, 34.0, 40.0, 45.0))
        session_calls: list[list[dict[str, Any]]] = []

        def session_factory(_: Path) -> _RecordingSession:
            calls: list[dict[str, Any]] = []
            session_calls.append(calls)
            return _RecordingSession(calls)

        case = BenchmarkCase(
            case_id="inspect_wiring_warm",
            method="inspect_wiring",
            state="warm",
            arguments={"target_path": "Assets/Benchmark.prefab", "script_filter": "BenchmarkMatch"},
            measured_trials=5,
            budget_sec=10.0,
        )
        runner = BenchmarkRunner(session_factory, clock=lambda: next(clock_values))

        measurement = runner.measure_case(case, Path("/project"))

        self.assertEqual(
            (
                (1.0, 2.0, 3.0, 4.0, 5.0),
                5,
                (2, 2, 2, 2, 2),
                "passed",
            ),
            (
                measurement.samples_sec,
                len(session_calls),
                tuple(len(calls) for calls in session_calls),
                measurement.status,
            ),
            msg="each warm trial must own one fresh session, one excluded warmup, and one measured call",
        )


class TestBenchmarkBudgetBoundaries(unittest.TestCase):
    def test_only_complete_medians_strictly_below_budget_pass(self) -> None:
        statuses = tuple(
            evaluate_case_status(
                (duration,) * 5,
                budget_sec=5.0,
                required_trials=5,
                complete=True,
            )
            for duration in (4.999, 5.0, 5.001)
        )

        self.assertEqual(
            ("passed", "failed", "failed"),
            statuses,
            msg="strict performance budgets must reject equal and above medians",
        )

    def test_partial_or_incomplete_sample_sets_fail_independently_of_duration(self) -> None:
        statuses = (
            evaluate_case_status(
                (1.0,) * 5,
                budget_sec=5.0,
                required_trials=5,
                complete=False,
            ),
            evaluate_case_status(
                (1.0,) * 4,
                budget_sec=5.0,
                required_trials=5,
                complete=True,
            ),
        )

        self.assertEqual(
            ("failed", "failed"),
            statuses,
            msg="partial responses and missing measured trials cannot pass a budget",
        )


class TestBenchmarkManifestDispatch(unittest.TestCase):
    def test_generated_projects_are_byte_stable_and_match_fixed_cardinalities(self) -> None:
        manifest = load_manifest(_MANIFEST_PATH)
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = generate_fixture(Path(first_dir), manifest)
            second = generate_fixture(Path(second_dir), manifest)

        self.assertEqual(
            (
                EXPECTED_CASE_IDS,
                EXPECTED_CARDINALITIES,
                first.fixture_hash,
                first.asset_files,
            ),
            (
                tuple(case.case_id for case in manifest.cases),
                dict(first.cardinalities),
                second.fixture_hash,
                second.asset_files,
            ),
            msg="independent fixture generations must have identical assets and the exact reviewed workload",
        )

    def test_material_summary_fixture_exercises_shader_main_texture_and_selected_properties(self) -> None:
        manifest = load_manifest(_MANIFEST_PATH)
        material_cases = tuple(
            case
            for case in manifest.cases
            if case.method == "inspect_material_asset"
        )
        expected_property_names = (
            "_MainTex",
            "_Benchmark00",
            "_Benchmark16",
            "_Benchmark32",
            "_Benchmark63",
        )
        self.assertEqual(
            (expected_property_names, expected_property_names),
            tuple(tuple(case.arguments["property_names"]) for case in material_cases),
            msg="both material-summary timing cases must select the main texture path",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            generate_fixture(project_root, manifest)
            cache = SessionCacheManager()
            cache.project_root = project_root
            response = cache.get_orchestrator().inspect_material_asset(
                **material_cases[0].arguments
            )

        self.assertEqual(
            (
                True,
                "Benchmark",
                "Assets/Benchmark/Shaders/Benchmark.shader",
                {
                    "name": "_MainTex",
                    "guid": "ffffffffffffffffffffffffffffffff",
                    "path": "Assets/Benchmark/Textures/BenchmarkMainTexture.png",
                },
                {
                    "_MainTex": {
                        "kind": "texture",
                        "guid": "ffffffffffffffffffffffffffffffff",
                    },
                    "_Benchmark00": {"kind": "float", "value": 0.0},
                    "_Benchmark16": {"kind": "float", "value": 1.6},
                    "_Benchmark32": {"kind": "float", "value": 3.2},
                    "_Benchmark63": {"kind": "float", "value": 6.3},
                },
                {
                    "texture_count": 1,
                    "float_count": 64,
                    "color_count": 0,
                    "int_count": 0,
                },
            ),
            (
                response.success,
                response.data["shader"]["name"],
                response.data["shader"]["path"],
                response.data["main_texture"],
                response.data["selected_properties"],
                response.data["counts"],
            ),
            msg="the canonical timed fixture must exercise the complete summary contract",
        )

    def test_all_seven_cases_execute_real_production_endpoints_with_complete_responses(self) -> None:
        manifest = load_manifest(_MANIFEST_PATH)
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            generate_fixture(project_root, manifest)

            def session_factory(root: Path):
                cache = SessionCacheManager()
                cache.project_root = root
                return cache.get_orchestrator()

            measurements = []
            for case in manifest.cases:
                one_trial = BenchmarkCase(
                    case.case_id,
                    case.method,
                    case.state,
                    case.arguments,
                    1,
                    case.budget_sec,
                )
                clock_values = iter((0.0, 0.001))
                runner = BenchmarkRunner(session_factory, clock=clock_values.__next__)
                measurements.append(runner.measure_case(one_trial, project_root))

        self.assertEqual(
            (EXPECTED_CASE_IDS, (True,) * 7, ("passed",) * 7),
            (
                tuple(item.case_id for item in measurements),
                tuple(item.complete for item in measurements),
                tuple(item.status for item in measurements),
            ),
            msg="every canonical case must dispatch through a real endpoint and return complete evidence",
        )

    def test_dependency_matrix_pins_all_fifty_six_audited_impacts(self) -> None:
        manifest = load_manifest(_MANIFEST_PATH)
        expected = {
            "#143": ("direct", "indirect", "direct", "indirect", "direct", "indirect", "direct"),
            "#144": ("direct",) * 7,
            "#145": ("non-impact",) * 7,
            "#146": ("non-impact",) * 7,
            "#147": ("non-impact",) * 6 + ("direct",),
            "#148": ("non-impact",) * 7,
            "#149": ("direct", "direct", "direct", "direct", "non-impact", "non-impact", "non-impact"),
            "#154": ("direct",) * 7,
        }

        observed = {
            issue: tuple(rows[case_id]["impact"] for case_id in EXPECTED_CASE_IDS)
            for issue, rows in manifest.dependency_mapping.items()
        }
        evidence = tuple(
            rows[case_id]["evidence"] for rows in manifest.dependency_mapping.values() for case_id in EXPECTED_CASE_IDS
        )

        self.assertEqual(
            (expected, 56, (True,) * 56),
            (observed, len(evidence), tuple(bool(item.strip()) for item in evidence)),
            msg="the report manifest must preserve every audited impact and non-empty evidence cell",
        )

    def test_every_dependency_impact_is_immutable(self) -> None:
        raw_manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        valid_impacts = ("direct", "indirect", "non-impact")

        for issue, rows in raw_manifest["dependency_mapping"].items():
            for case_id, cell in rows.items():
                replacement = next(impact for impact in valid_impacts if impact != cell["impact"])
                with self.subTest(issue=issue, case_id=case_id):
                    mutated = json.loads(json.dumps(raw_manifest))
                    mutated["dependency_mapping"][issue][case_id]["impact"] = replacement
                    with tempfile.TemporaryDirectory() as temp_dir:
                        path = Path(temp_dir) / "manifest.json"
                        path.write_text(json.dumps(mutated), encoding="utf-8")
                        with self.assertRaises(BenchmarkConfigurationError) as context:
                            load_manifest(path)

                    self.assertIn(
                        f"dependency_mapping.{issue}.{case_id}.impact",
                        str(context.exception),
                    )


class TestBenchmarkConfigurationAndReportFailures(unittest.TestCase):
    def test_missing_canonical_case_is_rejected_before_measurement(self) -> None:
        raw = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        raw["cases"] = raw["cases"][:-1]
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_path = Path(temp_dir) / "manifest.json"
            invalid_path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaises(BenchmarkConfigurationError) as context:
                load_manifest(invalid_path)

        self.assertIn(
            "case inventory",
            str(context.exception),
            msg="configuration failure must identify the missing canonical inventory before timing",
        )

    def test_every_canonical_manifest_field_is_fixed(self) -> None:
        mutations: tuple[tuple[tuple[str | int, ...], object], ...] = (
            (("schema_version",), "other-schema"),
            (("fixture", "version"), "other-fixture"),
            (("cases", 0, "method"), "inspect_hierarchy"),
            (("cases", 0, "state"), "warm"),
            (("cases", 0, "arguments", "script_filter"), "OtherFilter"),
            (("cases", 0, "measured_trials"), 4),
            (("cases", 0, "budget_sec"), 9.0),
        )

        for location, replacement in mutations:
            with self.subTest(location=location):
                raw: Any = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
                parent = raw
                for key in location[:-1]:
                    parent = parent[key]
                parent[location[-1]] = replacement
                with tempfile.TemporaryDirectory() as temp_dir:
                    invalid_path = Path(temp_dir) / "manifest.json"
                    invalid_path.write_text(json.dumps(raw), encoding="utf-8")

                    with self.assertRaises(BenchmarkConfigurationError) as context:
                        load_manifest(invalid_path)

                self.assertIn(
                    "fixed benchmark contract",
                    str(context.exception),
                    msg=f"tampered canonical field {location!r} must fail before timing",
                )

    def test_oversized_manifest_budget_uses_configuration_envelope(self) -> None:
        configuration_failure_exit_code = 2
        expected_measurement_calls = 0
        raw: Any = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        raw["cases"][0]["budget_sec"] = 10**400

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=AssertionError("measurement started"),
            ) as measure_case,
        ):
            temp_root = Path(temp_dir)
            invalid_path = temp_root / "manifest.json"
            report_path = temp_root / "report.json"
            invalid_path.write_text(json.dumps(raw), encoding="utf-8")

            result = run_benchmark_application(
                manifest_path=invalid_path,
                out_report=report_path,
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=True,
                baseline_ref=None,
                baseline_out=None,
            )

            self.assertEqual(
                (
                    configuration_failure_exit_code,
                    CONFIGURATION_CODE,
                    expected_measurement_calls,
                    False,
                ),
                (
                    result.exit_code,
                    result.code,
                    measure_case.call_count,
                    report_path.exists(),
                ),
                msg="oversized manifest budgets must fail before timing or report persistence",
            )

    def test_unhashable_manifest_enum_values_use_configuration_envelope(self) -> None:
        configuration_failure_exit_code = 2
        expected_measurement_calls = 0
        invalid_state: Any = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        invalid_state["cases"][0]["state"] = []
        invalid_impact: Any = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        invalid_impact["dependency_mapping"]["#143"]["inspect_wiring_cold"]["impact"] = []

        for label, raw in (
            ("case state", invalid_state),
            ("dependency impact", invalid_impact),
        ):
            with (
                self.subTest(field=label),
                tempfile.TemporaryDirectory() as temp_dir,
                patch.object(
                    BenchmarkRunner,
                    "measure_case",
                    side_effect=AssertionError("measurement started"),
                ) as measure_case,
            ):
                temp_root = Path(temp_dir)
                invalid_path = temp_root / "manifest.json"
                report_path = temp_root / "report.json"
                invalid_path.write_text(json.dumps(raw), encoding="utf-8")

                result = run_benchmark_application(
                    manifest_path=invalid_path,
                    out_report=report_path,
                    repository_root=_MANIFEST_PATH.parents[1],
                    enforce=True,
                    baseline_ref=None,
                    baseline_out=None,
                )

                self.assertEqual(
                    (
                        configuration_failure_exit_code,
                        CONFIGURATION_CODE,
                        expected_measurement_calls,
                        False,
                    ),
                    (
                        result.exit_code,
                        result.code,
                        measure_case.call_count,
                        report_path.exists(),
                    ),
                    msg=f"unhashable {label} must fail before timing or report persistence",
                )


class TestHistoricalBaselineReporting(unittest.TestCase):
    def test_same_host_fixture_requires_distinct_commits_and_equal_fingerprint(self) -> None:
        baseline = EnvironmentFingerprint(
            commit="baseline",
            operating_system="Linux",
            cpu="Example CPU",
            python_version="3.11.9",
            worker_count=8,
            fixture_hash="fixture",
        )
        current = EnvironmentFingerprint(
            commit="current",
            operating_system="Linux",
            cpu="Example CPU",
            python_version="3.11.9",
            worker_count=8,
            fixture_hash="fixture",
        )

        assert_same_host_fixture(baseline, current)

        same_commit = EnvironmentFingerprint(
            commit="baseline",
            operating_system="Linux",
            cpu="Example CPU",
            python_version="3.11.9",
            worker_count=8,
            fixture_hash="fixture",
        )
        with self.assertRaises(BenchmarkConfigurationError) as context:
            assert_same_host_fixture(baseline, same_commit)

        self.assertIn(
            "must differ",
            str(context.exception),
            msg="same-commit comparisons must be rejected explicitly",
        )

    def test_different_fixture_is_rejected_as_non_comparable(self) -> None:
        baseline = EnvironmentFingerprint("baseline", "Linux", "CPU", "3.11", 8, "old-fixture")
        current = EnvironmentFingerprint("current", "Linux", "CPU", "3.11", 8, "new-fixture")

        with self.assertRaises(BenchmarkConfigurationError) as context:
            assert_same_host_fixture(baseline, current)

        self.assertIn(
            "fixture_hash",
            str(context.exception),
            msg="comparison failure must name the differing fixture identity",
        )

    def test_checked_in_baseline_discloses_native_wiring_and_only_material_equivalent(self) -> None:
        baseline_path = _MANIFEST_PATH.parent / "baselines" / "pre-pr159.json"
        self.assertTrue(
            baseline_path.is_file(),
            msg=f"historical baseline must be checked in at {baseline_path}",
        )
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))

        self.assertEqual(
            [
                {
                    "case_ids": ["inspect_wiring_cold", "inspect_wiring_warm"],
                    "classification": "native",
                    "method": "inspect_wiring",
                    "arguments": {
                        "target_path": "Assets/Benchmark/InspectionTarget.prefab",
                        "script_filter": "BenchmarkMatch",
                        "summary_only": True,
                    },
                },
                {
                    "case_ids": [
                        "inspect_material_asset_summary_cold",
                        "inspect_material_asset_summary_warm",
                    ],
                    "classification": "historical-equivalent",
                    "method": "inspect_material_asset",
                    "actual_arguments": {
                        "target_path": "Assets/Benchmark/Materials/Material000.mat",
                    },
                    "unavailable_arguments": {
                        "mode": "summary",
                        "property_names": [
                            "_MainTex",
                            "_Benchmark00",
                            "_Benchmark16",
                            "_Benchmark32",
                            "_Benchmark63",
                        ],
                    },
                    "excluded_from_current_budget_semantics": True,
                },
            ],
            payload["historical_invocations"],
            msg="historical evidence must disclose native wiring and only the material equivalent",
        )


class TestBenchmarkReportFailureClassification(unittest.TestCase):
    def test_post_measurement_report_failure_has_dedicated_exit_contract(self) -> None:
        def failing_writer(_: Path, __: Any) -> None:
            raise BenchmarkReportWriteError("disk unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_benchmark_application(
                manifest_path=_MANIFEST_PATH,
                out_report=Path(temp_dir) / "report.json",
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=False,
                baseline_ref=None,
                baseline_out=None,
                report_writer=failing_writer,
            )

        self.assertEqual(
            (2, REPORT_WRITE_CODE, REPORT_WRITE_MESSAGE, None),
            (result.exit_code, result.code, result.message, result.report),
            msg="post-measurement persistence failure must not be mislabeled as invalid configuration",
        )

    @staticmethod
    def _failed_measurement(case: BenchmarkCase, _: Path) -> CaseMeasurement:
        samples = tuple(case.budget_sec for _ in range(case.measured_trials))
        return CaseMeasurement(
            case_id=case.case_id,
            samples_sec=samples,
            statistics=aggregate_samples(samples),
            response_codes=tuple("COMPLETE" for _ in samples),
            complete=True,
            status="failed",
        )

    def _nonfinite_baseline_outcome(self, median_json: str) -> tuple[int, str, int]:
        baseline_path = _MANIFEST_PATH.parent / "baselines" / "pre-pr159.json"
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        current_median = json.dumps(baseline["cases"][0]["statistics"]["median_sec"])
        baseline_json = json.dumps(baseline).replace(
            f'"median_sec": {current_median}',
            f'"median_sec": {median_json}',
            1,
        )
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=AssertionError("measurement started"),
            ) as measure_case,
        ):
            invalid_path = Path(temp_dir) / "nonfinite-baseline.json"
            invalid_path.write_text(baseline_json, encoding="utf-8")
            result = run_benchmark_application(
                manifest_path=_MANIFEST_PATH,
                out_report=Path(temp_dir) / "report.json",
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=True,
                baseline_ref=baseline["environment"]["commit"],
                baseline_out=invalid_path,
            )

        return result.exit_code, result.code, measure_case.call_count

    def test_cleanup_failure_keeps_report_write_exit_contract(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=self._failed_measurement,
            ),
            patch(
                "prefab_sentinel.benchmarking.report.os.replace",
                side_effect=OSError("report replacement failed"),
            ),
            patch.object(
                Path,
                "unlink",
                side_effect=OSError("temporary report cleanup failed"),
            ) as unlink,
        ):
            result = run_benchmark_application(
                manifest_path=_MANIFEST_PATH,
                out_report=Path(temp_dir) / "report.json",
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=True,
                baseline_ref=None,
                baseline_out=None,
            )

        self.assertEqual(
            (2, REPORT_WRITE_CODE, REPORT_WRITE_MESSAGE, None, 1),
            (
                result.exit_code,
                result.code,
                result.message,
                result.report,
                unlink.call_count,
            ),
            msg="temporary report cleanup failure must retain the dedicated report-write contract",
        )

    def test_budget_failure_persists_complete_invocation_metadata_in_both_modes(self) -> None:
        canonical = load_manifest(_MANIFEST_PATH)
        for enforce, expected_exit, expected_code in (
            (False, 0, "BENCHMARK_COMPLETED"),
            (True, 1, BUDGET_CODE),
        ):
            with self.subTest(enforce=enforce), tempfile.TemporaryDirectory() as temp_dir:
                report_path = Path(temp_dir) / "report.json"
                with patch.object(
                    BenchmarkRunner,
                    "measure_case",
                    side_effect=self._failed_measurement,
                ):
                    result = run_benchmark_application(
                        manifest_path=_MANIFEST_PATH,
                        out_report=report_path,
                        repository_root=_MANIFEST_PATH.parents[1],
                        enforce=enforce,
                        baseline_ref=None,
                        baseline_out=None,
                    )

                persisted = json.loads(report_path.read_text(encoding="utf-8"))
                first_case = canonical.cases[0]
                self.assertEqual(
                    (
                        expected_exit,
                        expected_code,
                        "failed",
                        first_case.method,
                        first_case.state,
                        dict(first_case.arguments),
                        first_case.measured_trials,
                        first_case.budget_sec,
                    ),
                    (
                        result.exit_code,
                        result.code,
                        persisted["cases"][0]["status"],
                        persisted["cases"][0]["method"],
                        persisted["cases"][0]["state"],
                        persisted["cases"][0]["arguments"],
                        persisted["cases"][0]["measured_trials"],
                        persisted["cases"][0]["budget_sec"],
                    ),
                    msg="application result and persisted report must retain exact failing invocation metadata",
                )

    def test_invalid_paired_baseline_is_rejected_before_any_measurement(self) -> None:
        baseline_path = _MANIFEST_PATH.parent / "baselines" / "pre-pr159.json"
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=AssertionError("measurement started"),
            ) as measure_case,
        ):
            result = run_benchmark_application(
                manifest_path=_MANIFEST_PATH,
                out_report=Path(temp_dir) / "report.json",
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=True,
                baseline_ref="not-the-baseline-commit",
                baseline_out=baseline_path,
            )

        self.assertEqual(
            (2, CONFIGURATION_CODE, 0),
            (result.exit_code, result.code, measure_case.call_count),
            msg="invalid baseline identity must stop before measured dispatch",
        )

    def test_malformed_baseline_json_is_rejected_before_measurement(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=AssertionError("measurement started"),
            ) as measure_case,
        ):
            baseline = Path(temp_dir) / "baseline.json"
            baseline.write_text("{not-json", encoding="utf-8")
            result = run_benchmark_application(
                manifest_path=_MANIFEST_PATH,
                out_report=Path(temp_dir) / "report.json",
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=True,
                baseline_ref="baseline",
                baseline_out=baseline,
            )

        self.assertEqual(
            (2, CONFIGURATION_CODE, 0),
            (result.exit_code, result.code, measure_case.call_count),
            msg="malformed baseline JSON must remain a pre-measurement configuration failure",
        )

    def test_malformed_baseline_environment_types_are_rejected_before_measurement(
        self,
    ) -> None:
        checked_baseline = _MANIFEST_PATH.parent / "baselines" / "pre-pr159.json"
        malformed_values: tuple[tuple[str, object], ...] = (
            ("commit", None),
            ("operating_system", 123),
            ("cpu", False),
            ("python_version", None),
            ("worker_count", "1"),
            ("worker_count", True),
            ("fixture_hash", 42),
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch(
                "prefab_sentinel.benchmarking.application.capture_environment"
            ) as capture_environment,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=AssertionError("measurement started"),
            ) as measure_case,
        ):
            for index, (field, malformed_value) in enumerate(malformed_values):
                with self.subTest(field=field, value=malformed_value):
                    baseline = json.loads(checked_baseline.read_text(encoding="utf-8"))
                    baseline_environment = baseline["environment"]
                    baseline_environment[field] = malformed_value
                    capture_environment.reset_mock()
                    capture_environment.return_value = EnvironmentFingerprint(
                        commit="current-revision",
                        operating_system=str(baseline_environment["operating_system"]),
                        cpu=str(baseline_environment["cpu"]),
                        python_version=str(baseline_environment["python_version"]),
                        worker_count=int(baseline_environment["worker_count"]),
                        fixture_hash=str(baseline_environment["fixture_hash"]),
                    )
                    baseline_path = Path(temp_dir) / f"baseline-{field}-{index}.json"
                    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
                    result = run_benchmark_application(
                        manifest_path=_MANIFEST_PATH,
                        out_report=Path(temp_dir) / f"report-{index}.json",
                        repository_root=_MANIFEST_PATH.parents[1],
                        enforce=True,
                        baseline_ref=str(baseline_environment["commit"]),
                        baseline_out=baseline_path,
                    )

                    self.assertEqual(
                        (2, CONFIGURATION_CODE, 0),
                        (result.exit_code, result.code, measure_case.call_count),
                        msg=(
                            "malformed baseline environment fields must fail before "
                            f"measurement: field={field!r}, value={malformed_value!r}"
                        ),
                    )
                    capture_environment.assert_called_once()

    def test_non_mapping_baseline_environment_is_rejected_before_measurement(
        self,
    ) -> None:
        configuration_failure_exit_code = 2
        expected_measurement_calls = 0
        checked_baseline = _MANIFEST_PATH.parent / "baselines" / "pre-pr159.json"
        malformed_environments: tuple[object, ...] = (None, [], "not-an-object", 0)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=AssertionError("measurement started"),
            ) as measure_case,
        ):
            for index, malformed_environment in enumerate(malformed_environments):
                with self.subTest(environment=malformed_environment):
                    baseline = json.loads(checked_baseline.read_text(encoding="utf-8"))
                    baseline_ref = baseline["environment"]["commit"]
                    baseline["environment"] = malformed_environment
                    baseline_path = Path(temp_dir) / f"baseline-container-{index}.json"
                    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
                    report_path = Path(temp_dir) / f"report-container-{index}.json"

                    result = run_benchmark_application(
                        manifest_path=_MANIFEST_PATH,
                        out_report=report_path,
                        repository_root=_MANIFEST_PATH.parents[1],
                        enforce=True,
                        baseline_ref=baseline_ref,
                        baseline_out=baseline_path,
                    )

                    self.assertEqual(
                        (
                            configuration_failure_exit_code,
                            CONFIGURATION_CODE,
                            expected_measurement_calls,
                            False,
                        ),
                        (
                            result.exit_code,
                            result.code,
                            measure_case.call_count,
                            report_path.exists(),
                        ),
                        msg=(
                            "non-object baseline environments must fail as configuration "
                            f"before measurement: value={malformed_environment!r}"
                        ),
                    )

    def test_nan_baseline_is_rejected_before_measurement(self) -> None:
        self.assertEqual(
            (2, CONFIGURATION_CODE, 0),
            self._nonfinite_baseline_outcome("NaN"),
            msg="NaN baseline medians must fail before benchmark measurement",
        )

    def test_infinite_baseline_is_rejected_before_measurement(self) -> None:
        self.assertEqual(
            (2, CONFIGURATION_CODE, 0),
            self._nonfinite_baseline_outcome("Infinity"),
            msg="infinite baseline medians must fail before benchmark measurement",
        )

    def test_overflowing_baseline_median_is_rejected_before_measurement(self) -> None:
        self.assertEqual(
            (2, CONFIGURATION_CODE, 0),
            self._nonfinite_baseline_outcome("1e999"),
            msg="overflowed baseline medians must fail before benchmark measurement",
        )

    def test_oversized_integer_baseline_is_rejected_before_measurement(self) -> None:
        overflowing_digit_count = 4000
        oversized_integer_json = "1" + ("0" * overflowing_digit_count)
        self.assertEqual(
            (2, CONFIGURATION_CODE, 0),
            self._nonfinite_baseline_outcome(oversized_integer_json),
            msg="oversized integer medians must fail through the configuration envelope",
        )

    def test_tampered_baseline_comparability_is_rejected_before_measurement(
        self,
    ) -> None:
        baseline_path = _MANIFEST_PATH.parent / "baselines" / "pre-pr159.json"
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        material_case = next(case for case in baseline["cases"] if case["id"] == "inspect_material_asset_summary_cold")
        material_case["current_budget_comparable"] = True
        material_case["invocation_classification"] = "native"

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=AssertionError("measurement started"),
            ) as measure_case,
        ):
            tampered_path = Path(temp_dir) / "tampered-baseline.json"
            tampered_path.write_text(json.dumps(baseline), encoding="utf-8")
            result = run_benchmark_application(
                manifest_path=_MANIFEST_PATH,
                out_report=Path(temp_dir) / "report.json",
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=True,
                baseline_ref=baseline["environment"]["commit"],
                baseline_out=tampered_path,
            )

        self.assertEqual(
            (2, CONFIGURATION_CODE, 0),
            (result.exit_code, result.code, measure_case.call_count),
            msg="canonical baseline comparability must be validated before measurement",
        )

    def test_exactly_one_baseline_option_is_rejected_before_measurement(self) -> None:
        baseline_path = _MANIFEST_PATH.parent / "baselines" / "pre-pr159.json"
        for baseline_ref, baseline_out in (
            ("baseline", None),
            (None, baseline_path),
        ):
            with (
                self.subTest(
                    baseline_ref=baseline_ref,
                    baseline_out=baseline_out,
                ),
                tempfile.TemporaryDirectory() as temp_dir,
                patch.object(
                    BenchmarkRunner,
                    "measure_case",
                    side_effect=AssertionError("measurement started"),
                ) as measure_case,
            ):
                result = run_benchmark_application(
                    manifest_path=_MANIFEST_PATH,
                    out_report=Path(temp_dir) / "report.json",
                    repository_root=_MANIFEST_PATH.parents[1],
                    enforce=False,
                    baseline_ref=baseline_ref,
                    baseline_out=baseline_out,
                )

            self.assertEqual(
                (2, CONFIGURATION_CODE, 0),
                (result.exit_code, result.code, measure_case.call_count),
                msg="baseline options are one required pair and must fail before timing",
            )

    def test_invalid_destination_is_rejected_before_measurement(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=AssertionError("measurement started"),
            ) as measure_case,
        ):
            result = run_benchmark_application(
                manifest_path=_MANIFEST_PATH,
                out_report=Path(temp_dir) / "missing" / "report.json",
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=False,
                baseline_ref=None,
                baseline_out=None,
            )

        self.assertEqual(
            (2, CONFIGURATION_CODE, 0),
            (result.exit_code, result.code, measure_case.call_count),
        )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_symlinked_report_parent_is_rejected_before_measurement(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=self._failed_measurement,
            ) as measure_case,
        ):
            root = Path(temp_dir)
            real_parent = root / "real"
            real_parent.mkdir()
            alias_parent = root / "alias"
            alias_parent.symlink_to(real_parent, target_is_directory=True)
            report_path = alias_parent / "report.json"

            result = run_benchmark_application(
                manifest_path=_MANIFEST_PATH,
                out_report=report_path,
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=False,
                baseline_ref=None,
                baseline_out=None,
            )

            self.assertEqual(
                (
                    2,
                    CONFIGURATION_CODE,
                    0,
                    False,
                ),
                (
                    result.exit_code,
                    result.code,
                    measure_case.call_count,
                    report_path.exists(),
                ),
                msg=(
                    "a lexical symlink in the report parent must fail before "
                    f"measurement or report persistence: {result!r}"
                ),
            )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_relative_report_under_symlinked_cwd_is_rejected_before_measurement(
        self,
    ) -> None:
        configuration_failure_exit_code = 2
        expected_measurement_calls = 0

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=self._failed_measurement,
            ) as measure_case,
        ):
            root = Path(temp_dir)
            real_parent = root / "real"
            real_parent.mkdir()
            alias_parent = root / "alias"
            alias_parent.symlink_to(real_parent, target_is_directory=True)
            previous_cwd = Path.cwd()
            try:
                os.chdir(alias_parent)
                with patch.dict(os.environ, {"PWD": str(alias_parent)}):
                    result = run_benchmark_application(
                        manifest_path=_MANIFEST_PATH,
                        out_report=Path("report.json"),
                        repository_root=_MANIFEST_PATH.parents[1],
                        enforce=False,
                        baseline_ref=None,
                        baseline_out=None,
                    )
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(
                (
                    configuration_failure_exit_code,
                    CONFIGURATION_CODE,
                    expected_measurement_calls,
                    False,
                ),
                (
                    result.exit_code,
                    result.code,
                    measure_case.call_count,
                    (real_parent / "report.json").exists(),
                ),
                msg=f"relative report through a symlinked cwd must fail preflight: {result!r}",
            )

    def test_output_cannot_overwrite_baseline_input(self) -> None:
        source = _MANIFEST_PATH.parent / "baselines" / "pre-pr159.json"
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=AssertionError("measurement started"),
            ) as measure_case,
        ):
            baseline = Path(temp_dir) / "baseline.json"
            baseline.write_bytes(source.read_bytes())
            before = baseline.read_bytes()
            baseline_ref = json.loads(before)["environment"]["commit"]
            result = run_benchmark_application(
                manifest_path=_MANIFEST_PATH,
                out_report=baseline,
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=False,
                baseline_ref=baseline_ref,
                baseline_out=baseline,
            )
            after = baseline.read_bytes()

        self.assertEqual(
            (2, CONFIGURATION_CODE, 0, before),
            (result.exit_code, result.code, measure_case.call_count, after),
        )

    def test_valid_paired_baseline_persists_per_case_comparisons(self) -> None:
        baseline_path = _MANIFEST_PATH.parent / "baselines" / "pre-pr159.json"
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline_environment = baseline["environment"]
        current_environment = EnvironmentFingerprint(
            commit="current-revision",
            operating_system=str(baseline_environment["operating_system"]),
            cpu=str(baseline_environment["cpu"]),
            python_version=str(baseline_environment["python_version"]),
            worker_count=int(baseline_environment["worker_count"]),
            fixture_hash=str(baseline_environment["fixture_hash"]),
        )
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch(
                "prefab_sentinel.benchmarking.application.capture_environment",
                return_value=current_environment,
            ) as capture_environment,
            patch.object(
                BenchmarkRunner,
                "measure_case",
                side_effect=self._failed_measurement,
            ),
        ):
            report_path = Path(temp_dir) / "report.json"
            result = run_benchmark_application(
                manifest_path=_MANIFEST_PATH,
                out_report=report_path,
                repository_root=_MANIFEST_PATH.parents[1],
                enforce=False,
                baseline_ref=baseline_environment["commit"],
                baseline_out=baseline_path,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        capture_environment.assert_called_once()
        comparisons = report["comparisons"]
        by_id = {row["case_id"]: row for row in comparisons}
        wiring = by_id["inspect_wiring_cold"]
        material = by_id["inspect_material_asset_summary_cold"]
        self.assertEqual(
            (
                0,
                "BENCHMARK_COMPLETED",
                7,
                wiring["current_median_sec"] - wiring["baseline_median_sec"],
                "slower",
                False,
                None,
                "not_comparable",
            ),
            (
                result.exit_code,
                result.code,
                len(comparisons),
                wiring["delta_sec"],
                wiring["status"],
                material["comparable"],
                material["delta_sec"],
                material["status"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
