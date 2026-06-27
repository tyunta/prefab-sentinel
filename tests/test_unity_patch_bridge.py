from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from prefab_sentinel.bridge_constants import PROTOCOL_VERSION as _EDITOR_CONTROL_PROTOCOL
from tests._assertion_helpers import assert_error_envelope
from tests._typing_helpers import require_mapping
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
        for key, saved_value in saved.items():
            if saved_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved_value

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
    ops = request_payload.get("ops", [])
    if not isinstance(ops, list):
        raise AssertionError(f"ops must be a list, got {type(ops).__name__}")
    return {
        "protocol_version": 2,
        "success": True,
        "severity": "info",
        "code": "SER_APPLY_OK",
        "message": "Applied via editor bridge.",
        "data": {"applied": len(ops)},
        "diagnostics": [],
    }


def _editor_bridge_unknown_action_response() -> dict[str, object]:
    """The envelope ``EditorBridge`` emits for an action-less request.

    Reproduces ``EditorBridge.WriteErrorResponse`` on the empty-action
    branch: a failure envelope stamped with the *editor-control*
    protocol version (not the patch protocol), which is exactly what
    masks the routing failure once the relay's protocol check runs.
    """
    return {
        "protocol_version": _EDITOR_CONTROL_PROTOCOL,
        "success": False,
        "severity": "error",
        "code": "EDITOR_BRIDGE_UNKNOWN_ACTION",
        "message": "Empty action field in request.",
        "data": {},
        "diagnostics": [],
    }


def _routing_faithful_response(
    request_payload: dict[str, object],
) -> dict[str, object]:
    """Dispatch a request the way the resident ``EditorBridge`` does.

    ``EditorBridge`` peeks at the request's ``action`` field: an empty
    action is rejected with ``EDITOR_BRIDGE_UNKNOWN_ACTION``; a
    non-empty action unclaimed by the editor-control / runtime action
    sets falls through to ``UnityPatchBridge``, which applies the
    patch.  This builder reproduces both branches so a dropped
    discriminator fails the round-trip rather than passing against a
    builder that ignores ``action``.
    """
    action = str(request_payload.get("action", ""))
    if not action:
        return _editor_bridge_unknown_action_response()
    return _success_response(request_payload)


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

        data = require_mapping(result["data"], "round-trip response data")
        self.assertEqual(
            ("SER_APPLY_OK", 1, "editor"),
            (
                result["code"],
                data["applied"],
                data["bridge_mode"],
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

    def test_patch_apply_round_trips_through_routing_faithful_responder(self) -> None:
        """Issue #63: a patch request carrying the ``action`` discriminator
        is routed to the patch bridge by a responder that dispatches
        exactly as ``EditorBridge`` does, and round-trips to a
        ``SER_APPLY_OK`` success envelope.  A dropped discriminator would
        land on the empty-action branch and surface
        ``BRIDGE_PROTOCOL_VERSION`` instead.
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
            with EditorBridgeResponder(watch_path, _routing_faithful_response):
                result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )
        self.assertEqual(
            (True, "SER_APPLY_OK"),
            (bool(result["success"]), result["code"]),
            msg=(
                "a patch request must carry the action discriminator so a "
                "routing-faithful responder reaches the patch bridge and "
                f"round-trips to SER_APPLY_OK; got envelope={result!r}"
            ),
        )

    def test_patch_request_carries_patch_apply_action_discriminator(self) -> None:
        """Issue #63: the relay stamps ``action='patch_apply'`` onto every
        patch request so the resident ``EditorBridge`` dispatches it to
        ``UnityPatchBridge``.
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
                "ops": [],
            }
            with EditorBridgeResponder(watch_path, _success_response) as responder:
                _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )
        self.assertEqual(
            "patch_apply",
            responder.observed_requests[0].get("action"),
            msg=(
                "every patch request written to the watch directory must "
                "carry action='patch_apply'; got observed request "
                f"{responder.observed_requests[0]!r}"
            ),
        )

    def test_empty_action_request_is_rejected_by_routing_faithful_responder(self) -> None:
        """Issue #63: the routing-faithful responder reproduces
        ``EditorBridge``'s empty-action branch — a request with no
        ``action`` is rejected with ``EDITOR_BRIDGE_UNKNOWN_ACTION``.
        This pins the responder's fidelity: without it the round-trip
        test above would pass against a responder that proves nothing
        about routing.
        """
        response = _routing_faithful_response(
            {"protocol_version": 2, "target": "Assets/Test.prefab", "ops": []}
        )
        self.assertEqual(
            (False, "EDITOR_BRIDGE_UNKNOWN_ACTION"),
            (bool(response["success"]), response["code"]),
            msg=(
                "an action-less request must be rejected with "
                f"EDITOR_BRIDGE_UNKNOWN_ACTION; got {response!r}"
            ),
        )

    def test_patch_apply_discriminator_does_not_collide_with_editor_control_actions(
        self,
    ) -> None:
        """Issue #63: ``patch_apply`` must stay in the patch-bridge
        fall-through space — it must not be a member of the
        editor-control action set, or it would be routed to the wrong
        bridge.
        """
        from prefab_sentinel.editor_bridge import SUPPORTED_ACTIONS

        self.assertNotIn(
            "patch_apply",
            SUPPORTED_ACTIONS,
            msg=(
                "the patch_apply discriminator collides with the "
                "editor-control action set; pick a value outside it."
            ),
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

    def test_set_op_file_id_target_survives_to_the_bridge_request(self) -> None:
        """Issue #37: a set op identified by ``file_id`` (no ``component``)
        passes schema validation and the file_id reaches the editor-bridge
        request payload — the wire encoder must not drop the new field."""
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
                        "file_id": "300",
                        "path": "m_Enabled",
                        "value": 1,
                    }
                ],
            }
            with EditorBridgeResponder(watch_path, _success_response) as responder:
                exit_code, parsed = _invoke_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )

        self.assertEqual(
            (0, True),
            (exit_code, bool(parsed["success"])),
            msg=(
                "a file_id-targeted set op must pass schema validation; "
                f"got exit={exit_code!r} envelope={parsed!r}"
            ),
        )
        observed_op = responder.observed_requests[0]["ops"][0]
        self.assertEqual(
            "300",
            observed_op.get("file_id"),
            msg=(
                "the bridge wire encoder must forward op.file_id to the "
                "editor-bridge request (issue #37)."
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
