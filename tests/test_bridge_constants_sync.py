"""T25: drift-checker sync test + bridge-constants surface invariants.

Invokes the live drift checker against the current repository and asserts
no drift is present.  This covers the regression path: any commit that
advances one of the three invariants without the others will fail here.

Issue #270 collapses the dispatch surface to a single Editor Bridge path;
the constants module now exposes only the names consumed by surviving
callers, and the batchmode-era names must not be importable.
"""

from __future__ import annotations

import unittest

import pytest

import prefab_sentinel.bridge_constants as bridge_constants
from scripts.check_bridge_constants import main as check_main

# Issue #167: this module invokes the live drift checker against the
# original (un-mutated) repository checkout, so its assertions cannot
# observe mutations applied to ``prefab_sentinel/``.  The marker is the
# inclusion mechanism for repository-synchrony tests; mutmut's pytest
# selection excludes it via a single ``-m`` filter.
pytestmark = pytest.mark.source_text_invariant

_SURVIVING_NAMES = (
    "BRIDGE_WATCH_DIR_ENV",
    "UNITY_PROJECT_PATH_ENV",
    "UNITY_LOG_FILE_ENV",
    "UNITY_TIMEOUT_SEC_ENV",
    "PROTOCOL_VERSION",
    "VALID_SEVERITIES",
    "CONSOLE_LOG_BUFFER_MAX_ENTRIES",
)

_BATCHMODE_ONLY_NAMES = (
    "BRIDGE_MODE_ENV",
    "UNITY_COMMAND_ENV",
    "UNITY_EXECUTE_METHOD_ENV",
)


class BridgeConstantsSyncTests(unittest.TestCase):
    def test_no_drift_in_repository(self) -> None:
        self.assertEqual(0, check_main())

    def test_surviving_exports_are_importable(self) -> None:
        for name in _SURVIVING_NAMES:
            self.assertTrue(
                hasattr(bridge_constants, name),
                f"bridge_constants must expose {name}",
            )

    def test_batchmode_only_names_are_absent(self) -> None:
        for name in _BATCHMODE_ONLY_NAMES:
            # ``from prefab_sentinel.bridge_constants import <name>``: the
            # CPython IMPORT_FROM opcode raises ImportError when the
            # named attribute is missing on the module.  Exec'ing the
            # exact statement form is the only way to pin the import-
            # statement contract (``importlib.import_module`` + getattr
            # would raise AttributeError, a different failure mode).
            with self.assertRaises(ImportError) as ctx:
                exec(
                    f"from prefab_sentinel.bridge_constants import {name}",
                    {},
                )
            self.assertIn(name, str(ctx.exception))


class RuntimeValidationConfigSurfaceTests(unittest.TestCase):
    """Issue #270 — the runtime-validation config module no longer
    exposes the batchmode-CLI helpers and execute-method constants.
    """

    _BATCHMODE_ONLY_NAMES = (
        "load_runtime_config",
        "build_runtime_command",
        "UNITY_RUNTIME_EXECUTE_METHOD_ENV",
        "DEFAULT_RUNTIME_EXECUTE_METHOD",
    )

    def test_batchmode_only_names_are_absent(self) -> None:
        from prefab_sentinel.services.runtime_validation import config

        for name in self._BATCHMODE_ONLY_NAMES:
            self.assertFalse(
                hasattr(config, name),
                f"runtime_validation.config must not expose {name}",
            )


if __name__ == "__main__":
    unittest.main()
