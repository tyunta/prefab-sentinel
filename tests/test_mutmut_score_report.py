"""Tests for ``scripts/mutmut_score_report.py`` (issue #169).

The parser is the unit-testable seam (string-in / record-out); the
formatter rows pin Markdown, CSV, and JSON shapes; the audited-only
and ``--module`` filter rows pin the restriction logic; the
subprocess-failure row pins the distinct exit code (``4``) and stderr
passthrough.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

# Make ``scripts/mutmut_score_report.py`` importable as
# ``mutmut_score_report`` from the test process.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import mutmut_score_report  # noqa: E402


def _parse_records_from_json(
    text: str,
) -> dict[str, mutmut_score_report.ModuleRecord]:
    """Reverse of :func:`mutmut_score_report.format_json` for round-trip tests.

    The production script does not need this inverse; it lives here so
    the JSON formatter test can assert structural fidelity without
    polluting the script's public surface.
    """
    payload = json.loads(text)
    records: dict[str, mutmut_score_report.ModuleRecord] = {}
    for entry in payload["records"]:
        records[entry["module"]] = mutmut_score_report.ModuleRecord(
            module=entry["module"],
            killed=entry["killed"],
            survived=entry["survived"],
            timeout=entry["timeout"],
            not_checked=entry["not_checked"],
        )
    return records


_SAMPLE_MULTI_MODULE_RESULTS = """\
prefab_sentinel.services.reference_resolver.xǁReferenceResolverServiceǁread_text__mutmut_1: killed
prefab_sentinel.services.reference_resolver.xǁReferenceResolverServiceǁread_text__mutmut_2: survived
prefab_sentinel.services.reference_resolver.xǁReferenceResolverServiceǁread_text__mutmut_3: timeout
prefab_sentinel.services.reference_resolver.xǁReferenceResolverServiceǁread_text__mutmut_4: not_checked
prefab_sentinel.services.prefab_variant.overrides.parse_overrides__mutmut_1: killed
prefab_sentinel.services.prefab_variant.overrides.parse_overrides__mutmut_2: killed
prefab_sentinel.services.prefab_variant.overrides.parse_overrides__mutmut_3: survived
prefab_sentinel.orchestrator_postcondition._validate_postcondition_schema__mutmut_1: killed
"""


class MutmutResultsParserTests(unittest.TestCase):
    """Pure parser rows: string in, record out."""

    def test_multi_module_input_produces_per_module_record(self) -> None:
        records = mutmut_score_report.parse_mutmut_results(
            _SAMPLE_MULTI_MODULE_RESULTS
        )
        self.assertIn(
            "prefab_sentinel.services.reference_resolver", records
        )
        self.assertIn(
            "prefab_sentinel.services.prefab_variant.overrides", records
        )
        self.assertIn(
            "prefab_sentinel.orchestrator_postcondition", records
        )
        ref = records["prefab_sentinel.services.reference_resolver"]
        self.assertEqual(1, ref.killed)
        self.assertEqual(1, ref.survived)
        self.assertEqual(1, ref.timeout)
        self.assertEqual(1, ref.not_checked)
        self.assertEqual(3, ref.total)  # not_checked excluded from denominator
        # killed (1) + timeout (1) over total (3) = 66.6...%
        self.assertAlmostEqual(2 / 3 * 100.0, ref.score, places=2)
        variant = records[
            "prefab_sentinel.services.prefab_variant.overrides"
        ]
        self.assertEqual(2, variant.killed)
        self.assertEqual(1, variant.survived)
        self.assertEqual(0, variant.timeout)

    def test_empty_input_returns_empty_record(self) -> None:
        self.assertEqual(
            {}, mutmut_score_report.parse_mutmut_results("")
        )

    def test_malformed_lines_are_skipped_silently(self) -> None:
        text = (
            "garbage line\n"
            "another nonsense\n"
            "prefab_sentinel.services.reference_resolver.xǁRǁread__mutmut_1: killed\n"
            "incomplete:\n"
            ": killed\n"
        )
        records = mutmut_score_report.parse_mutmut_results(text)
        self.assertEqual(1, len(records))
        self.assertEqual(
            1,
            records[
                "prefab_sentinel.services.reference_resolver"
            ].killed,
        )

    def test_module_filter_restricts_to_named_module(self) -> None:
        records = mutmut_score_report.parse_mutmut_results(
            _SAMPLE_MULTI_MODULE_RESULTS,
            module_filter="prefab_sentinel.orchestrator_postcondition",
        )
        self.assertEqual(
            ["prefab_sentinel.orchestrator_postcondition"],
            list(records.keys()),
        )


class MutmutResultsFormatterTests(unittest.TestCase):
    """Markdown / CSV / JSON formatter pins."""

    def _records(self) -> dict[str, mutmut_score_report.ModuleRecord]:
        return mutmut_score_report.parse_mutmut_results(
            _SAMPLE_MULTI_MODULE_RESULTS
        )

    def test_markdown_table_renders_one_header_and_one_row_per_module(self) -> None:
        text = mutmut_score_report.format_markdown(self._records())
        lines = text.strip().splitlines()
        # 1 header + 1 separator + 3 data rows
        self.assertEqual(5, len(lines))
        self.assertTrue(lines[0].startswith("| module |"))
        self.assertTrue(lines[1].startswith("|---|"))
        # Score column matches derived value to one decimal place.
        ref_row = next(
            line
            for line in lines
            if "reference_resolver" in line
        )
        self.assertIn("66.7%", ref_row)

    def test_csv_format_carries_run_metadata_and_columns(self) -> None:
        metadata = mutmut_score_report.RunMetadata(
            run_date="2026-05-05",
            mutmut_version="3.5.0",
            parallelism="180",
        )
        text = mutmut_score_report.format_csv(self._records(), metadata)
        lines = text.strip().splitlines()
        header = lines[0].split(",")
        for column in (
            "run_date",
            "mutmut_version",
            "parallelism",
            "module",
            "killed",
            "survived",
            "timeout",
            "not_checked",
            "total",
            "score",
        ):
            self.assertIn(column, header)
        for data_line in lines[1:]:
            self.assertIn("2026-05-05", data_line)
            self.assertIn("3.5.0", data_line)
            self.assertIn("180", data_line)

    def test_json_round_trips_through_parse_records_from_json(self) -> None:
        records = self._records()
        text = mutmut_score_report.format_json(records)
        round_tripped = _parse_records_from_json(text)
        self.assertEqual(set(records.keys()), set(round_tripped.keys()))
        for module, record in records.items():
            other = round_tripped[module]
            self.assertEqual(record.killed, other.killed)
            self.assertEqual(record.survived, other.survived)
            self.assertEqual(record.timeout, other.timeout)
            self.assertEqual(record.not_checked, other.not_checked)


class MutmutResultsAuditedFilterTests(unittest.TestCase):
    """``filter_audited`` restricts the record set to README §14.5."""

    def test_audited_only_filter_restricts_to_audited_module_list(self) -> None:
        # The sample contains an off-list module added solely to test
        # the filter (a fictional ``prefab_sentinel.unrelated``).
        text = (
            _SAMPLE_MULTI_MODULE_RESULTS
            + "prefab_sentinel.unrelated.module.func__mutmut_1: killed\n"
        )
        records = mutmut_score_report.parse_mutmut_results(text)
        filtered = mutmut_score_report.filter_audited(records)
        for module in filtered:
            self.assertTrue(
                any(
                    module == name or module.startswith(name + ".")
                    for name in mutmut_score_report.AUDITED_MODULES
                ),
                f"module {module!r} leaked through the audited filter",
            )
        self.assertNotIn(
            "prefab_sentinel.unrelated.module", filtered
        )


class MutmutResultsSubprocessFailureTests(unittest.TestCase):
    """Subprocess-failure row: distinct exit code and stderr passthrough."""

    def test_non_zero_subprocess_returncode_yields_distinct_exit_code(
        self,
    ) -> None:
        outcome = mutmut_score_report.SubprocessOutcome(
            returncode=2,
            stdout="",
            stderr="mutmut: fatal error reading working tree\n",
        )
        captured_stderr = io.StringIO()
        with (
            mock.patch.object(
                mutmut_score_report,
                "run_mutmut_results",
                return_value=outcome,
            ),
            redirect_stderr(captured_stderr),
        ):
            rc = mutmut_score_report.main(["--format", "markdown"])
        self.assertEqual(
            mutmut_score_report.MUTMUT_SUBPROCESS_FAILURE_EXIT_CODE, rc
        )
        # Distinct from zero, from the test-failure code (1), and from
        # the missing-runner code (2) used elsewhere.
        self.assertNotEqual(0, rc)
        self.assertNotEqual(1, rc)
        self.assertNotEqual(2, rc)
        self.assertIn(
            "mutmut: fatal error reading working tree",
            captured_stderr.getvalue(),
        )

    def test_empty_parse_yields_zero_exit_with_empty_table(self) -> None:
        outcome = mutmut_score_report.SubprocessOutcome(
            returncode=0,
            stdout="",
            stderr="",
        )
        captured_stdout = io.StringIO()
        with (
            mock.patch.object(
                mutmut_score_report,
                "run_mutmut_results",
                return_value=outcome,
            ),
            redirect_stdout(captured_stdout),
        ):
            rc = mutmut_score_report.main(["--format", "markdown"])
        self.assertEqual(0, rc)
        self.assertIn("| module |", captured_stdout.getvalue())


class MutmutResultsCliEndToEndTests(unittest.TestCase):
    """End-to-end CLI rows for ``--module`` and ``--audited-only``."""

    def test_cli_module_filter_emits_only_named_module(self) -> None:
        outcome = mutmut_score_report.SubprocessOutcome(
            returncode=0,
            stdout=_SAMPLE_MULTI_MODULE_RESULTS,
            stderr="",
        )
        captured = io.StringIO()
        with (
            mock.patch.object(
                mutmut_score_report,
                "run_mutmut_results",
                return_value=outcome,
            ),
            redirect_stdout(captured),
        ):
            rc = mutmut_score_report.main(
                [
                    "--module",
                    "prefab_sentinel.orchestrator_postcondition",
                    "--format",
                    "json",
                ]
            )
        self.assertEqual(0, rc)
        payload = json.loads(captured.getvalue())
        self.assertEqual(1, len(payload["records"]))
        self.assertEqual(
            "prefab_sentinel.orchestrator_postcondition",
            payload["records"][0]["module"],
        )

    def test_cli_audited_only_strips_off_list_modules(self) -> None:
        text = (
            _SAMPLE_MULTI_MODULE_RESULTS
            + "prefab_sentinel.unrelated.module.func__mutmut_1: killed\n"
        )
        outcome = mutmut_score_report.SubprocessOutcome(
            returncode=0,
            stdout=text,
            stderr="",
        )
        captured = io.StringIO()
        with (
            mock.patch.object(
                mutmut_score_report,
                "run_mutmut_results",
                return_value=outcome,
            ),
            redirect_stdout(captured),
        ):
            rc = mutmut_score_report.main(
                ["--audited-only", "--format", "json"]
            )
        self.assertEqual(0, rc)
        payload = json.loads(captured.getvalue())
        modules = [entry["module"] for entry in payload["records"]]
        self.assertNotIn("prefab_sentinel.unrelated.module", modules)
        for module in modules:
            self.assertTrue(
                any(
                    module == name or module.startswith(name + ".")
                    for name in mutmut_score_report.AUDITED_MODULES
                )
            )


if __name__ == "__main__":
    unittest.main()
