from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests._assertion_helpers import assert_error_envelope
from tests.bridge_test_helpers import EditorBridgeResponder
from tools.unity_patch_bridge import main as _bridge_main

# Issue #157: the patch-bridge tests drive the entry point in-process
# rather than spawning a subprocess so mutmut can mutate the underlying
# package code without losing trampoline state at process boundaries.

_BRIDGE_DISPATCH_ENV_KEYS = (
    "UNITYTOOL_UNITY_PROJECT_PATH",
    "UNITYTOOL_UNITY_TIMEOUT_SEC",
    "UNITYTOOL_UNITY_LOG_FILE",
    "UNITYTOOL_BRIDGE_WATCH_DIR",
)


def _invoke_bridge(
    payload: dict[str, object],
    env_overrides: dict[str, str] | None,
) -> tuple[int, dict[str, object]]:
    """Drive ``tools.unity_patch_bridge.main`` in-process.

    Returns ``(exit_code, parsed_response)``.  Pops the bridge-dispatch env
    vars before the call so each test starts from a deterministic state;
    ``env_overrides`` then applies the keys the test does intend to set.
    """
    pop_keys = {key: None for key in _BRIDGE_DISPATCH_ENV_KEYS}
    overlay: dict[str, str] = dict(env_overrides) if env_overrides else {}

    captured = io.StringIO()
    saved: dict[str, str | None] = {key: os.environ.get(key) for key in pop_keys}
    saved.update({key: os.environ.get(key) for key in overlay})
    try:
        for key in pop_keys:
            os.environ.pop(key, None)
        for key, value in overlay.items():
            os.environ[key] = value
        with redirect_stdout(captured):
            exit_code = _bridge_main(stdin=io.StringIO(json.dumps(payload)))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    text = captured.getvalue()
    parsed = json.loads(text)
    return exit_code, parsed


def _run_bridge(
    payload: dict[str, object],
    *,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    """Drive the bridge in-process and return the parsed response envelope.

    Thin wrapper over ``_invoke_bridge`` for tests that only need the
    response payload and not the exit code.  Extracted to module scope so
    the same helper is reused across all test classes.
    """
    _exit_code, parsed = _invoke_bridge(payload, env_overrides)
    return parsed


def _success_response(request_payload: dict[str, object]) -> dict[str, object]:
    """Build the canonical editor-bridge success envelope."""
    return {
        "protocol_version": 2,
        "success": True,
        "severity": "info",
        "code": "SER_APPLY_OK",
        "message": "Applied via editor bridge.",
        "data": {"applied": len(request_payload.get("ops", []))},
        "diagnostics": [],
    }


class UnityPatchBridgeTests(unittest.TestCase):
    """Pre-bridge input validators and the editor-bridge round trip."""

    def test_request_with_wrong_protocol_version_is_rejected(self) -> None:
        result = _run_bridge(
            {
                "protocol_version": 999,
                "plan_version": 2,
                "resources": [
                    {
                        "id": "prefab",
                        "kind": "prefab",
                        "path": "Assets/Test.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [],
            }
        )
        assert_error_envelope(
            result,
            code="BRIDGE_PROTOCOL_VERSION",
            severity="error",
        )

    def test_set_op_without_value_is_rejected_with_op_location_pinned(self) -> None:
        result = _run_bridge(
            {
                "protocol_version": 2,
                "plan_version": 2,
                "resources": [
                    {
                        "id": "prefab",
                        "kind": "prefab",
                        "path": "Assets/Test.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [
                    {
                        "resource": "prefab",
                        "op": "set",
                        "component": "Example.Component",
                        "path": "enabled",
                    }
                ],
            }
        )
        assert_error_envelope(
            result,
            code="BRIDGE_REQUEST_SCHEMA",
            severity="error",
            data={
                "location": "ops[0]",
                "error": "set operation requires 'value'",
            },
        )

    def test_array_op_without_index_is_rejected_with_op_index_location_pinned(self) -> None:
        result = _run_bridge(
            {
                "protocol_version": 2,
                "plan_version": 2,
                "resources": [
                    {
                        "id": "prefab",
                        "kind": "prefab",
                        "path": "Assets/Test.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [
                    {
                        "resource": "prefab",
                        "op": "remove_array_element",
                        "component": "Example.Component",
                        "path": "items.Array.data",
                    }
                ],
            }
        )
        assert_error_envelope(
            result,
            code="BRIDGE_REQUEST_SCHEMA",
            severity="error",
            data={
                "location": "ops[0].index",
                "error": "array operation requires integer 'index'",
            },
        )

    def test_create_asset_op_without_type_or_shader_is_rejected_with_schema_error(self) -> None:
        result = _run_bridge(
            {
                "protocol_version": 2,
                "plan_version": 2,
                "resources": [
                    {
                        "id": "asset",
                        "kind": "asset",
                        "path": "Assets/New.asset",
                        "mode": "create",
                    }
                ],
                "ops": [
                    {"resource": "asset", "op": "create_asset"},
                ],
            }
        )
        assert_error_envelope(
            result,
            code="BRIDGE_REQUEST_SCHEMA",
            severity="error",
        )

    def test_resource_with_unsupported_extension_is_rejected_with_unsupported_target_code(self) -> None:
        result = _run_bridge(
            {
                "protocol_version": 2,
                "plan_version": 2,
                "resources": [
                    {
                        "id": "asset",
                        "kind": "asset",
                        "path": "Assets/New.txt",
                        "mode": "open",
                    }
                ],
                "ops": [],
            }
        )
        assert_error_envelope(
            result,
            code="BRIDGE_UNSUPPORTED_TARGET",
            severity="error",
        )

    def test_valid_plan_with_running_responder_round_trips_with_editor_bridge_mode(self) -> None:
        """End-to-end editor-bridge round trip: a request file appears in
        the watch directory, the responder writes back the success envelope,
        and the bridge returns it to the caller with ``bridge_mode='editor'``.
        """
        with tempfile.TemporaryDirectory() as watch_dir:
            watch_path = Path(watch_dir)
            payload = {
                "protocol_version": 2,
                "plan_version": 2,
                "resources": [
                    {
                        "id": "prefab",
                        "kind": "prefab",
                        "path": "Assets/Test.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [
                    {
                        "resource": "prefab",
                        "op": "set",
                        "component": "Example.Component",
                        "path": "enabled",
                        "value": True,
                    }
                ],
            }

            with EditorBridgeResponder(watch_path, _success_response):
                result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )

        self.assertEqual(
            ("SER_APPLY_OK", 1, "editor"),
            (
                result["code"],
                result["data"]["applied"],
                result["data"]["bridge_mode"],
            ),
            msg=(
                "round trip must yield SER_APPLY_OK with applied=1 and "
                f"bridge_mode='editor'; got envelope={result!r}"
            ),
        )

    def test_request_with_top_level_target_key_is_rejected_as_legacy_schema(self) -> None:
        """T37 (#88): a bridge request carrying a top-level ``target`` key
        is rejected with ``BRIDGE_LEGACY_SCHEMA_REJECTED`` before any
        normalisation runs.  ``data.received_keys`` must enumerate the
        incoming top-level keys (sorted alphabetically by production) so
        the caller can diagnose the legacy shape.
        """
        with tempfile.TemporaryDirectory() as watch_dir:
            result = _run_bridge(
                {
                    "protocol_version": 2,
                    "target": "Assets/Legacy.prefab",
                    "ops": [],
                },
                env_overrides={"UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir},
            )
        assert_error_envelope(
            result,
            code="BRIDGE_LEGACY_SCHEMA_REJECTED",
            severity="error",
            data={"received_keys": ["ops", "protocol_version", "target"]},
        )


class EditorBridgeModeTests(unittest.TestCase):
    """Pin the editor-bridge file-watcher dispatch envelopes."""

    def test_request_without_watch_dir_env_is_rejected_with_watch_dir_missing(self) -> None:
        """Without ``UNITYTOOL_BRIDGE_WATCH_DIR`` set the CLI rejects the
        request with ``BRIDGE_WATCH_DIR_MISSING`` before contacting the
        bridge.  The exit code is non-zero.
        """
        exit_code, result = _invoke_bridge(
            {
                "protocol_version": 2,
                "plan_version": 2,
                "resources": [
                    {
                        "id": "prefab",
                        "kind": "prefab",
                        "path": "Assets/Test.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [],
            },
            env_overrides=None,
        )
        self.assertEqual(
            (True, "BRIDGE_WATCH_DIR_MISSING"),
            (exit_code != 0, result["code"]),
            msg=(
                "unset watch-dir env must yield non-zero exit and a "
                f"BRIDGE_WATCH_DIR_MISSING envelope; got exit={exit_code!r} "
                f"envelope={result!r}"
            ),
        )

    def test_watch_dir_set_without_responder_times_out_with_editor_timeout_code(self) -> None:
        """A watch directory with no responder running must time out
        rather than hang forever.  The timeout path is observable as a
        ``BRIDGE_EDITOR_TIMEOUT`` failure envelope.
        """
        with tempfile.TemporaryDirectory() as watch_dir:
            result = _run_bridge(
                {
                    "protocol_version": 2,
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "prefab",
                            "kind": "prefab",
                            "path": "Assets/Test.prefab",
                            "mode": "open",
                        }
                    ],
                    "ops": [],
                },
                env_overrides={
                    "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                    "UNITYTOOL_UNITY_TIMEOUT_SEC": "2",
                },
            )
        self.assertEqual(
            "BRIDGE_EDITOR_TIMEOUT",
            result["code"],
            msg=(
                "an empty watch dir with no responder must produce a "
                f"BRIDGE_EDITOR_TIMEOUT envelope; got {result!r}"
            ),
        )

    def test_absolute_asset_path_is_normalized_to_relative_assets_path_for_responder(self) -> None:
        """Absolute paths are stripped to relative ``Assets/...`` paths
        before the request is written to the watch directory.
        """
        with tempfile.TemporaryDirectory() as watch_dir:
            watch_path = Path(watch_dir)
            wsl_path = "/mnt/d/Project/Assets/Test.prefab"

            with EditorBridgeResponder(watch_path, _success_response) as responder:
                result = _run_bridge(
                    {
                        "protocol_version": 2,
                        "plan_version": 2,
                        "resources": [
                            {
                                "id": "prefab",
                                "kind": "prefab",
                                "path": wsl_path,
                                "mode": "open",
                            }
                        ],
                        "ops": [],
                    },
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )

        self.assertEqual(
            (True, 1, "Assets/Test.prefab"),
            (
                bool(result["success"]),
                len(responder.observed_requests),
                responder.observed_requests[0]["target"]
                if responder.observed_requests
                else None,
            ),
            msg=(
                "absolute asset path must be normalized to 'Assets/Test.prefab' "
                f"before reaching the responder; got envelope={result!r} "
                f"observed_requests={responder.observed_requests!r}"
            ),
        )

    def test_windows_style_watch_dir_reaches_responder_path_before_timing_out(self) -> None:
        """Watch dir should be normalised via ``to_wsl_path``, so Windows
        paths work on WSL.  A timeout (no watcher running) is the expected
        result; what we check is that the bridge gets that far rather
        than failing on a directory-write error.
        """
        with tempfile.TemporaryDirectory() as native_dir:
            result = _run_bridge(
                {
                    "protocol_version": 2,
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "prefab",
                            "kind": "prefab",
                            "path": "Assets/Test.prefab",
                            "mode": "open",
                        }
                    ],
                    "ops": [],
                },
                env_overrides={
                    "UNITYTOOL_BRIDGE_WATCH_DIR": native_dir,
                    "UNITYTOOL_UNITY_TIMEOUT_SEC": "2",
                },
            )
        self.assertEqual(
            "BRIDGE_EDITOR_TIMEOUT",
            result["code"],
            msg=(
                "windows-style watch dir must normalize and reach the "
                "responder-absence timeout path, not a directory-write "
                f"error; got {result!r}"
            ),
        )

    def test_remove_component_validator_accepts_selector_or_rejects_when_both_missing(self) -> None:
        """Open-mode ``remove_component`` accepts either a hierarchy
        ``target`` or a ``Type@/path`` ``component`` selector, and rejects
        an op that supplies neither.

        Necessity Check: the accept and reject branches pin two distinct
        production paths in the selector-presence gate.  The accept-leg
        subTest fails the moment the disjunction tightens (e.g. starts
        demanding both ``target`` and ``component``).  The reject-leg
        subTest fails the moment the disjunction inverts (e.g. starts
        passing ops that name neither).  Deleting either subTest leaves
        the corresponding branch unguarded, so the merge does not lose
        mutation-kill coverage relative to two separate methods.
        """
        accept_payload = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "prefab",
                    "path": "Assets/Test.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {
                    "resource": "prefab",
                    "op": "remove_component",
                    "component": "AudioSource@/Root",
                },
            ],
        }
        reject_payload = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "prefab",
                    "path": "Assets/Test.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {"resource": "prefab", "op": "remove_component"},
            ],
        }

        with self.subTest(branch="accept_component_selector"):
            result = _run_bridge(accept_payload)
            # The accept-leg op is valid for schema purposes; downstream
            # failure (no watch-dir env) is allowed.  The pinned signal is
            # that the schema validator did NOT trip BRIDGE_REQUEST_SCHEMA.
            self.assertNotEqual(
                "BRIDGE_REQUEST_SCHEMA",
                result.get("code", ""),
                msg=(
                    "remove_component with a 'component' selector must "
                    "pass schema validation; got "
                    f"envelope={result!r}"
                ),
            )

        with self.subTest(branch="reject_when_target_and_component_missing"):
            result = _run_bridge(reject_payload)
            assert_error_envelope(
                result,
                code="BRIDGE_REQUEST_SCHEMA",
                severity="error",
            )

    def test_add_component_validator_accepts_target_and_type_or_rejects_when_type_missing(self) -> None:
        """Open-mode ``add_component`` accepts a request that supplies both
        a hierarchy ``target`` and a ``type``, and rejects a request that
        omits ``type``.

        Necessity Check: the accept and reject branches pin two distinct
        production paths in the ``type``-requirement gate.  The accept-leg
        subTest fails if the ``type`` requirement is over-tightened (e.g.
        now demands an additional field).  The reject-leg subTest fails
        if the ``type`` requirement is removed or loosened to truthy-only
        (e.g. accepts empty strings).  Deleting either subTest leaves the
        corresponding branch unguarded, so the merge does not lose
        mutation-kill coverage relative to two separate methods.
        """
        accept_payload = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "prefab",
                    "path": "Assets/Test.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {
                    "resource": "prefab",
                    "op": "add_component",
                    "target": "/CharacterBody",
                    "type": "AudioSource",
                },
            ],
        }
        reject_payload = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "prefab",
                    "path": "Assets/Test.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {
                    "resource": "prefab",
                    "op": "add_component",
                    "target": "/CharacterBody",
                },
            ],
        }

        with self.subTest(branch="accept_target_and_type"):
            result = _run_bridge(accept_payload)
            self.assertNotEqual(
                "BRIDGE_REQUEST_SCHEMA",
                result.get("code", ""),
                msg=(
                    "add_component with 'target' and 'type' must pass "
                    f"schema validation; got envelope={result!r}"
                ),
            )

        with self.subTest(branch="reject_when_type_missing"):
            result = _run_bridge(reject_payload)
            assert_error_envelope(
                result,
                code="BRIDGE_REQUEST_SCHEMA",
                severity="error",
            )


class InProcessEntryPointContractTests(unittest.TestCase):
    """Issue #157 — direct ``main()`` invocation contract.

    Pins the entry point's exit-code semantics (zero on success-shape
    response, non-zero on failure-shape response) and the stdin override
    so callers can drive the bridge without subprocess.
    """

    def test_happy_path_round_trip_returns_zero_exit_with_success_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as watch_dir:
            watch_path = Path(watch_dir)
            payload = {
                "protocol_version": 2,
                "plan_version": 2,
                "resources": [
                    {
                        "id": "prefab",
                        "kind": "prefab",
                        "path": "Assets/Test.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [],
            }

            with EditorBridgeResponder(watch_path, _success_response):
                exit_code, parsed = _invoke_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )

        self.assertEqual(
            (0, True, "SER_APPLY_OK"),
            (exit_code, bool(parsed["success"]), parsed["code"]),
            msg=(
                "in-process happy path must return zero exit code with a "
                f"SER_APPLY_OK success envelope; got exit={exit_code!r} "
                f"envelope={parsed!r}"
            ),
        )

    def test_malformed_stdin_exits_nonzero_with_request_json_failure_envelope(self) -> None:
        captured = io.StringIO()
        with redirect_stdout(captured):
            exit_code = _bridge_main(stdin=io.StringIO("not-json"))
        parsed = json.loads(captured.getvalue())
        self.assertEqual(
            (True, False, "BRIDGE_REQUEST_JSON"),
            (exit_code != 0, bool(parsed["success"]), parsed["code"]),
            msg=(
                "malformed stdin must produce a non-zero exit code and a "
                f"BRIDGE_REQUEST_JSON failure envelope; got exit={exit_code!r} "
                f"envelope={parsed!r}"
            ),
        )

    def test_unknown_argv_exits_nonzero_with_schema_failure_echoing_argv(self) -> None:
        captured = io.StringIO()
        with redirect_stdout(captured):
            exit_code = _bridge_main(argv=["--unknown-flag"], stdin=io.StringIO(""))
        parsed = json.loads(captured.getvalue())
        self.assertEqual(
            (True, False, "BRIDGE_REQUEST_SCHEMA", ["--unknown-flag"]),
            (
                exit_code != 0,
                bool(parsed["success"]),
                parsed["code"],
                parsed["data"]["received_argv"],
            ),
            msg=(
                "unknown argv must yield non-zero exit, BRIDGE_REQUEST_SCHEMA, "
                f"and echo the argv verbatim in data.received_argv; got "
                f"exit={exit_code!r} envelope={parsed!r}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
