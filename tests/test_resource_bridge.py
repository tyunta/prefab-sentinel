"""Branch-coverage uplift for ``prefab_sentinel.services.serialized_object.resource_bridge`` (issue #188).

Pins env parsing, state resolution, suffix mapping, and allow-list checks
by value.

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


def _set_env(value: str) -> mock._patch_dict:  # type: ignore[name-defined]
    """Return a patch.dict that sets ``UNITYTOOL_PATCH_BRIDGE`` to *value*."""
    return mock.patch.dict(os.environ, {"UNITYTOOL_PATCH_BRIDGE": value}, clear=False)


class ResourceBridgeEnvTests(unittest.TestCase):
    """Pin every branch in ``load_bridge_command_from_env``."""

    def setUp(self) -> None:
        # Always start with a clean slate so host shell exports do not leak.
        self._saved = os.environ.pop("UNITYTOOL_PATCH_BRIDGE", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["UNITYTOOL_PATCH_BRIDGE"] = self._saved
        else:
            os.environ.pop("UNITYTOOL_PATCH_BRIDGE", None)

    def test_env_unset_returns_none_pair(self) -> None:
        cmd, err = resource_bridge.load_bridge_command_from_env()
        self.assertIsNone(cmd)
        self.assertIsNone(err)

    def test_quoted_argv_round_trips_without_quotes(self) -> None:
        with _set_env('"python.exe" "-m" "prefab_sentinel"'):
            cmd, err = resource_bridge.load_bridge_command_from_env()
        self.assertIsNone(err)
        self.assertEqual(("python.exe", "-m", "prefab_sentinel"), cmd)

    def test_unbalanced_quote_returns_parse_failure_string(self) -> None:
        with _set_env('"unbalanced'):
            cmd, err = resource_bridge.load_bridge_command_from_env()
        self.assertIsNone(cmd)
        self.assertIsNotNone(err)
        self.assertIn("Failed to parse UNITYTOOL_PATCH_BRIDGE", err)

    def test_empty_after_split_returns_documented_error(self) -> None:
        # The "did not produce a command" branch is a defensive guard
        # against future ``shlex.split`` behaviour changes; with the
        # current stdlib it is only reachable by monkey-patching
        # ``shlex.split`` to return an empty tuple.
        with (
            _set_env("anything"),
            mock.patch.object(
                resource_bridge.shlex, "split", return_value=()
            ),
        ):
            cmd, err = resource_bridge.load_bridge_command_from_env()
        self.assertIsNone(cmd)
        self.assertEqual(
            "UNITYTOOL_PATCH_BRIDGE did not produce a command.", err
        )


class ResourceBridgeStateTests(unittest.TestCase):
    """Pin ``build_bridge_state`` branches, suffix mapping, and allow-list."""

    def setUp(self) -> None:
        self._saved = os.environ.pop("UNITYTOOL_PATCH_BRIDGE", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["UNITYTOOL_PATCH_BRIDGE"] = self._saved
        else:
            os.environ.pop("UNITYTOOL_PATCH_BRIDGE", None)

    def test_state_uses_env_when_command_absent(self) -> None:
        with _set_env("uv run prefab-sentinel-unity-bridge"):
            state = resource_bridge.build_bridge_state(
                bridge_command=None, bridge_timeout_sec=30.0
            )
        self.assertEqual(("uv", "run", "prefab-sentinel-unity-bridge"), state.command)
        self.assertEqual(30.0, state.timeout_sec)
        self.assertIsNone(state.error)

    def test_state_clamps_minimum_timeout(self) -> None:
        state = resource_bridge.build_bridge_state(
            bridge_command=("python",), bridge_timeout_sec=0.1
        )
        self.assertEqual(1.0, state.timeout_sec)

    def test_state_falls_back_on_invalid_timeout(self) -> None:
        state = resource_bridge.build_bridge_state(
            bridge_command=("python",), bridge_timeout_sec="not-a-number"  # type: ignore[arg-type]
        )
        # Documented fallback is 120.0 (matches build_bridge_state body).
        self.assertEqual(120.0, state.timeout_sec)

    # ---- target / allow-list / kind ---------------------------------------

    def test_target_check_recognizes_each_supported_suffix(self) -> None:
        for suffix in (".prefab", ".unity", ".asset", ".mat", ".anim", ".controller"):
            self.assertTrue(
                resource_bridge.is_unity_bridge_target(Path(f"x{suffix}")),
                f"{suffix} should be recognized as a Unity bridge target",
            )

    def test_target_check_rejects_unknown_suffix(self) -> None:
        self.assertFalse(resource_bridge.is_unity_bridge_target(Path("x.txt")))
        self.assertFalse(resource_bridge.is_unity_bridge_target(Path("x.json")))

    def test_command_allow_list_is_case_insensitive_and_path_insensitive(
        self,
    ) -> None:
        # Every documented head, in lower / mixed / path-prefixed forms.
        for cmd in (
            ("python",),
            ("PYTHON.EXE",),
            ("/usr/bin/python3",),
            ("uv",),
            ("uvx.exe",),
            ("prefab-sentinel-unity-bridge.exe",),
            ("prefab-sentinel-unity-serialized-object-bridge",),
        ):
            self.assertTrue(
                resource_bridge.is_bridge_command_allowed(cmd),
                f"{cmd!r} should be allowed",
            )
        for cmd in (("not-allowed",), ("bash",), ("python-something",)):
            self.assertFalse(
                resource_bridge.is_bridge_command_allowed(cmd),
                f"{cmd!r} should be rejected",
            )

    def test_resource_kind_maps_each_suffix(self) -> None:
        for suffix, expected in (
            (".prefab", "prefab"),
            (".unity", "scene"),
            (".asset", "asset"),
            (".mat", "material"),
            (".anim", "animation"),
            (".controller", "controller"),
        ):
            self.assertEqual(
                expected,
                resource_bridge.infer_bridge_resource_kind(Path(f"x{suffix}")),
                f"{suffix} should map to {expected!r}",
            )

    def test_resource_kind_falls_back_to_asset(self) -> None:
        self.assertEqual(
            "asset", resource_bridge.infer_bridge_resource_kind(Path("x.unknown"))
        )

    def test_infer_resource_kind_recognizes_json(self) -> None:
        self.assertEqual("json", resource_bridge.infer_resource_kind(Path("x.json")))

    def test_infer_resource_kind_falls_through_to_bridge_inference(self) -> None:
        self.assertEqual(
            "prefab", resource_bridge.infer_resource_kind(Path("x.prefab"))
        )
        self.assertEqual(
            "asset", resource_bridge.infer_resource_kind(Path("x.unknown"))
        )


if __name__ == "__main__":
    unittest.main()
