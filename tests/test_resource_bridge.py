"""Behavioural pins for ``prefab_sentinel.services.serialized_object.resource_bridge``.

The configuration-side helpers in ``resource_bridge`` are pure functions
over ``UNITYTOOL_PATCH_BRIDGE`` and the documented allow-lists.  Each
test below pins the documented value (the ``(command, error)`` pair, the
exact suffix-to-kind mapping, the boolean classification) by exact
equality so a mutation that swaps allow-list membership, suffix mapping,
or env-parse phrasing is killed by the same assertion message.

Branches in the target module not covered: the subprocess-invoking helpers
``apply_with_unity_bridge`` / ``build_unity_bridge_request`` /
``parse_bridge_response`` (re-exported from ``resource_bridge_invoke``)
are integration-tested by ``test_d2_patch_dispatch_executor_revert.py``
and by the bridge smoke contract; the rows here cover the configuration-
side helpers that ``resource_bridge`` itself defines.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from prefab_sentinel.services.serialized_object import resource_bridge

# The documented env var name for the bridge command.
_BRIDGE_ENV = "UNITYTOOL_PATCH_BRIDGE"

# The documented sentinel pair returned by ``load_bridge_command_from_env``
# when the env var is unset (per the helper's docstring).
_UNSET_PAIR: tuple[None, None] = (None, None)

# The documented "did not produce a command" phrase emitted when
# ``shlex.split`` yields an empty tuple (defensive guard branch).
_EMPTY_AFTER_SPLIT_PHRASE = "UNITYTOOL_PATCH_BRIDGE did not produce a command."

# The documented prefix carried by the parse-failure error string.
_PARSE_FAILURE_PREFIX = "Failed to parse UNITYTOOL_PATCH_BRIDGE"


def _set_env(value: str) -> mock._patch_dict:  # type: ignore[name-defined]
    """Return a patch.dict that sets ``UNITYTOOL_PATCH_BRIDGE`` to *value*."""
    return mock.patch.dict(os.environ, {_BRIDGE_ENV: value}, clear=False)


class _BridgeEnvIsolationMixin:
    """Drop any inherited ``UNITYTOOL_PATCH_BRIDGE`` value before each test.

    Without this, a value the host shell exports (or that a previous test
    failed to clean up) would silently change the parse-result branch.
    """

    def setUp(self) -> None:
        super().setUp()
        self._saved = os.environ.pop(_BRIDGE_ENV, None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ[_BRIDGE_ENV] = self._saved
        else:
            os.environ.pop(_BRIDGE_ENV, None)
        super().tearDown()


class LoadBridgeCommandEnvTests(_BridgeEnvIsolationMixin, unittest.TestCase):
    """Pin every branch of ``load_bridge_command_from_env``."""

    def test_unset_env_var_returns_documented_none_pair(self) -> None:
        # Sentinel pair contract: both members are None when env is unset.
        result = resource_bridge.load_bridge_command_from_env()

        self.assertEqual(_UNSET_PAIR, result)

    def test_quoted_argv_is_parsed_into_unquoted_tuple(self) -> None:
        with _set_env('"python.exe" "-m" "prefab_sentinel"'):
            cmd, err = resource_bridge.load_bridge_command_from_env()

        self.assertEqual(("python.exe", "-m", "prefab_sentinel"), cmd)
        self.assertIsNone(err)

    def test_unbalanced_quote_yields_none_command_and_parse_failure_string(
        self,
    ) -> None:
        with _set_env('"unbalanced'):
            cmd, err = resource_bridge.load_bridge_command_from_env()

        # Behavioural contract: command is suppressed AND the documented
        # failure-prefix is surfaced — the caller distinguishes "not set"
        # (None, None) from "set but malformed" (None, prefix).
        self.assertIsNone(cmd)
        self.assertIsNotNone(err, msg="parse failure must surface a non-None error")
        # mypy/Pylance: post-assert err is non-None.
        assert err is not None
        self.assertTrue(
            err.startswith(_PARSE_FAILURE_PREFIX),
            msg=(
                f"parse-failure string must begin with the documented prefix "
                f"{_PARSE_FAILURE_PREFIX!r}; got {err!r}"
            ),
        )

    def test_empty_after_split_returns_documented_phrase(self) -> None:
        # The "did not produce a command" branch is a defensive guard
        # against future ``shlex.split`` behaviour changes; with the
        # current stdlib it is only reachable by stubbing the boundary.
        with (
            _set_env("anything"),
            mock.patch.object(resource_bridge.shlex, "split", return_value=()),
        ):
            cmd, err = resource_bridge.load_bridge_command_from_env()

        self.assertEqual((None, _EMPTY_AFTER_SPLIT_PHRASE), (cmd, err))


class BuildBridgeStateTests(_BridgeEnvIsolationMixin, unittest.TestCase):
    """Pin ``build_bridge_state`` resolution and timeout-clamp branches."""

    def test_state_uses_env_argv_when_no_explicit_command(self) -> None:
        with _set_env("uv run prefab-sentinel-unity-bridge"):
            state = resource_bridge.build_bridge_state(
                bridge_command=None, bridge_timeout_sec=30.0
            )

        # Pin all three observable fields together so a mutation that
        # only changes one (e.g. drops the env path) is killed here.
        self.assertEqual(
            (("uv", "run", "prefab-sentinel-unity-bridge"), 30.0, None),
            (state.command, state.timeout_sec, state.error),
        )

    def test_state_clamps_sub_minimum_timeout_to_documented_floor(self) -> None:
        # Documented floor (``max(1.0, timeout)``) is 1.0 second.
        floor_seconds = 1.0
        below_floor = 0.1

        state = resource_bridge.build_bridge_state(
            bridge_command=("python",), bridge_timeout_sec=below_floor
        )

        self.assertEqual(floor_seconds, state.timeout_sec)

    def test_state_falls_back_to_documented_default_on_invalid_timeout(
        self,
    ) -> None:
        # Documented fallback when float() raises is 120.0 seconds.
        documented_default_seconds = 120.0

        state = resource_bridge.build_bridge_state(
            bridge_command=("python",),
            bridge_timeout_sec="not-a-number",  # type: ignore[arg-type]
        )

        self.assertEqual(documented_default_seconds, state.timeout_sec)


class IsUnityBridgeTargetTests(unittest.TestCase):
    """Pin the documented Unity-asset suffix allow-list."""

    # Every documented Unity-asset suffix (from ``UNITY_BRIDGE_SUPPORTED_SUFFIXES``).
    _ALLOWED_SUFFIXES = (".prefab", ".unity", ".asset", ".mat", ".anim", ".controller")
    # Representative non-asset suffixes that must be rejected.
    _REJECTED_SUFFIXES = (".txt", ".json", ".png", ".cs")

    def test_allowed_suffixes_classify_as_bridge_targets(self) -> None:
        observed = {
            suffix: resource_bridge.is_unity_bridge_target(Path(f"x{suffix}"))
            for suffix in self._ALLOWED_SUFFIXES
        }
        expected = dict.fromkeys(self._ALLOWED_SUFFIXES, True)

        self.assertEqual(expected, observed)

    def test_rejected_suffixes_are_not_classified_as_bridge_targets(self) -> None:
        observed = {
            suffix: resource_bridge.is_unity_bridge_target(Path(f"x{suffix}"))
            for suffix in self._REJECTED_SUFFIXES
        }
        expected = dict.fromkeys(self._REJECTED_SUFFIXES, False)

        self.assertEqual(expected, observed)


class IsBridgeCommandAllowedTests(unittest.TestCase):
    """Pin the documented allow-list across case and path-prefix variants."""

    _ALLOWED_INPUTS: tuple[tuple[str, ...], ...] = (
        # lowercase form
        ("python",),
        # uppercase + extension form
        ("PYTHON.EXE",),
        # leading directory must be stripped before lookup
        ("/usr/bin/python3",),
        ("uv",),
        ("uvx.exe",),
        ("prefab-sentinel-unity-bridge.exe",),
        ("prefab-sentinel-unity-serialized-object-bridge",),
    )
    _REJECTED_INPUTS: tuple[tuple[str, ...], ...] = (
        ("not-allowed",),
        ("bash",),
        # near-miss: shares a prefix with an allowed name
        ("python-something",),
    )

    def test_allowed_command_inputs_pass_allow_list(self) -> None:
        observed = {
            cmd: resource_bridge.is_bridge_command_allowed(cmd)
            for cmd in self._ALLOWED_INPUTS
        }
        expected = dict.fromkeys(self._ALLOWED_INPUTS, True)

        self.assertEqual(expected, observed)

    def test_rejected_command_inputs_fail_allow_list(self) -> None:
        observed = {
            cmd: resource_bridge.is_bridge_command_allowed(cmd)
            for cmd in self._REJECTED_INPUTS
        }
        expected = dict.fromkeys(self._REJECTED_INPUTS, False)

        self.assertEqual(expected, observed)


class InferBridgeResourceKindTests(unittest.TestCase):
    """Pin the documented suffix-to-kind mapping."""

    # Documented mapping from ``UNITY_BRIDGE_KIND_BY_SUFFIX``.
    _SUFFIX_TO_KIND: dict[str, str] = {
        ".prefab": "prefab",
        ".unity": "scene",
        ".asset": "asset",
        ".mat": "material",
        ".anim": "animation",
        ".controller": "controller",
    }

    def test_each_documented_suffix_maps_to_documented_kind(self) -> None:
        observed = {
            suffix: resource_bridge.infer_bridge_resource_kind(Path(f"x{suffix}"))
            for suffix in self._SUFFIX_TO_KIND
        }

        self.assertEqual(self._SUFFIX_TO_KIND, observed)

    def test_unknown_suffix_falls_back_to_asset_kind(self) -> None:
        observed = resource_bridge.infer_bridge_resource_kind(Path("x.unknown"))

        self.assertEqual("asset", observed)


class InferResourceKindTests(unittest.TestCase):
    """Pin the json-aware kind inference layered above the bridge mapping."""

    def test_json_suffix_returns_json_kind(self) -> None:
        # ``infer_resource_kind`` adds the ``.json -> "json"`` branch on
        # top of the bridge mapping.
        self.assertEqual("json", resource_bridge.infer_resource_kind(Path("x.json")))

    def test_non_json_suffix_falls_through_to_bridge_inference(self) -> None:
        # Pin both a documented bridge-suffix passthrough and the
        # unknown-suffix fallback together so a mutation that drops the
        # passthrough branch is killed here.
        observed = (
            resource_bridge.infer_resource_kind(Path("x.prefab")),
            resource_bridge.infer_resource_kind(Path("x.unknown")),
        )

        self.assertEqual(("prefab", "asset"), observed)


if __name__ == "__main__":
    unittest.main()
