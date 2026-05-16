"""Python-side wrapper for the C# xUnit Screenshot view-allowlist coverage.

Issue #222 Phase 3: the bridge-side ``ScreenshotViewAllowlistClassifier``
is exercised end-to-end by the C# xUnit harness under ``tests/csharp/``.
This module surfaces that coverage in the Python unit-test summary
under a single opt-in environment variable so the default invocation
stays unaffected by the .NET runtime dependency.

Skip semantics: when ``PREFAB_SENTINEL_RUN_CSHARP_TESTS`` is unset (or
empty), every test in this module is skipped at collection time with
a reason that names the variable. When it is set to any non-empty
value, the tests invoke ``run_csharp_tests`` (a ``FileNotFoundError``
from a missing .NET runtime then surfaces under the project's
infrastructure-exception contract).
"""

from __future__ import annotations

import os
import unittest

from tests._csharp_harness import OPT_IN_ENV_VAR, run_csharp_tests


def _is_opt_in_set() -> bool:
    return bool(os.environ.get(OPT_IN_ENV_VAR))


# Filter that scopes the runner to the classifier theory rows. The
# expression matches every member of ``ScreenshotViewAllowlistClassifierTests``
# via the VSTest ``FullyQualifiedName~`` prefix-match operator.
_CLASSIFIER_FILTER = (
    "FullyQualifiedName~PrefabSentinel.Tests.ScreenshotViewAllowlistClassifierTests"
)

# Documented theory-row total: 2 (accept) + 10 (reject variants) + 4
# (empty-accept-set degenerate) = 16. Changing the C# theory must
# update this constant in lockstep so a silently-dropped row trips the
# wrapper.
_EXPECTED_CLASSIFIER_ROWS = 16


@unittest.skipUnless(
    _is_opt_in_set(),
    f"Skipped without opt-in: set {OPT_IN_ENV_VAR}=1 to run the C# xUnit harness.",
)
class CsharpScreenshotViewAllowlistTests(unittest.TestCase):
    """C-5 / C-6: surface the C# classifier coverage in the Python summary."""

    def test_classifier_rows_all_pass(self) -> None:
        outcome = run_csharp_tests(filter_expression=_CLASSIFIER_FILTER)

        self.assertEqual(
            0,
            outcome.failed,
            msg=(
                f"C# classifier rows reported {outcome.failed} failure(s); "
                f"exit_code={outcome.exit_code}; captured stdout:\n{outcome.stdout}"
            ),
        )
        self.assertEqual(
            _EXPECTED_CLASSIFIER_ROWS,
            outcome.passed,
            msg=(
                f"C# classifier passed-count mismatch: expected "
                f"{_EXPECTED_CLASSIFIER_ROWS}, observed {outcome.passed}; "
                f"captured stdout:\n{outcome.stdout}"
            ),
        )
        self.assertEqual(
            0,
            outcome.exit_code,
            msg=(
                f"C# runner exited non-zero ({outcome.exit_code}); "
                f"captured stderr:\n{outcome.stderr}"
            ),
        )


@unittest.skipUnless(
    _is_opt_in_set(),
    f"Skipped without opt-in: set {OPT_IN_ENV_VAR}=1 to run the C# xUnit harness.",
)
class CsharpHarnessHelperTests(unittest.TestCase):
    """C-7: the harness helper returns a structured outcome with parsed counts."""

    # ``HarnessSanityTests`` declares exactly one ``[Fact]`` so the
    # row count is deterministic; pinning the exact value (rather than
    # ``>= 1``) catches a regression that silently drops the row from
    # the runner's discovery surface.
    _EXPECTED_SANITY_ROWS = 1

    def test_helper_returns_structured_outcome_for_sanity_row(self) -> None:
        outcome = run_csharp_tests(
            filter_expression="FullyQualifiedName~HarnessSanityTests"
        )

        self.assertEqual(0, outcome.exit_code, msg=outcome.stderr)
        self.assertEqual(
            0,
            outcome.failed,
            msg=f"sanity-row failed-count must be zero; stdout:\n{outcome.stdout}",
        )
        self.assertEqual(
            self._EXPECTED_SANITY_ROWS,
            outcome.passed,
            msg=(
                f"sanity-row passed-count must equal "
                f"{self._EXPECTED_SANITY_ROWS}; stdout:\n{outcome.stdout}"
            ),
        )


@unittest.skipIf(
    _is_opt_in_set(),
    f"Verifies the no-opt-in skip path; only runs when {OPT_IN_ENV_VAR} is unset.",
)
class CsharpHarnessCollectionSkipTests(unittest.TestCase):
    """C-6 negative path — the opt-in gate produces a documented skip
    reason that names the environment variable, so an operator looking
    at the suite output understands what flag to flip.

    Only runs in the no-opt-in regime: ``@unittest.skipUnless`` only
    populates ``__unittest_skip_why__`` on the wrapper classes when the
    condition is False, so the introspection is meaningful only then.
    """

    def test_skip_reason_names_the_opt_in_environment_variable(self) -> None:
        # Introspect the live ``@unittest.skipUnless`` decorator on each
        # wrapper class via ``__unittest_skip_why__``.  Asserting against
        # a locally-rebuilt f-string would be tautological because the
        # interpolation guarantees the substring; reading the real
        # attribute tests the deployed gate itself.
        for cls in (
            CsharpScreenshotViewAllowlistTests,
            CsharpHarnessHelperTests,
        ):
            with self.subTest(cls=cls.__name__):
                why = getattr(cls, "__unittest_skip_why__", "")
                self.assertIn(
                    OPT_IN_ENV_VAR,
                    why,
                    msg=(
                        f"{cls.__name__} skip reason must name "
                        f"{OPT_IN_ENV_VAR!r} so operators discover the gate; "
                        f"observed: {why!r}"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
