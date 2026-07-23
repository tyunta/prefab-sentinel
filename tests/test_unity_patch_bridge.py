from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from prefab_sentinel.bridge_constants import PROTOCOL_VERSION as _EDITOR_CONTROL_PROTOCOL
from prefab_sentinel.services.serialized_object.resource_bridge_invoke import (
    parse_bridge_response,
)
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


def _valid_set_op(resource_id: str) -> dict[str, object]:
    return {
        "resource": resource_id,
        "op": "set",
        "component": "Example.Component",
        "path": "enabled",
        "value": True,
    }


def _success_data(request_payload: dict[str, object]) -> dict[str, object]:
    target = request_payload.get("target")
    ops = request_payload.get("ops")
    if not isinstance(target, str):
        raise AssertionError(f"target must be a string, got {type(target).__name__}")
    if not isinstance(ops, list):
        raise AssertionError(f"ops must be a list, got {type(ops).__name__}")
    return {
        "target": target,
        "op_count": len(ops),
        "applied": len(ops),
        "read_only": False,
        "executed": True,
        "protocol_version": 2,
        "created_results": [],
    }


def _success_response(request_payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocol_version": 2,
        "success": True,
        "severity": "info",
        "code": "SER_APPLY_OK",
        "message": "Applied via editor bridge.",
        "data": _success_data(request_payload),
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

    def test_request_protocol_version_requires_an_integer(self) -> None:
        result = _run_bridge(
            {
                "protocol_version": 2.0,
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

    def test_editor_response_protocol_version_requires_an_integer(self) -> None:
        from tools.unity_patch_bridge import _finalize_unity_response

        result = _finalize_unity_response(
            payload={
                "protocol_version": 2.0,
                "success": True,
                "severity": "info",
                "code": "SER_APPLY_OK",
                "message": "Bridge apply completed.",
                "data": {},
                "diagnostics": [],
            },
            target="Assets/Test.prefab",
            op_count=0,
        )

        self.assertEqual(
            (False, "BRIDGE_PROTOCOL_VERSION"),
            (result["success"], result["code"]),
        )

    def test_editor_response_applied_string_is_rejected(self) -> None:
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
            "ops": [_valid_set_op("prefab")],
        }
        with tempfile.TemporaryDirectory() as watch_dir:
            with EditorBridgeResponder(
                Path(watch_dir),
                lambda request: {
                    **_success_response(request),
                    "data": {**_success_data(request), "applied": "1"},
                },
            ):
                result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )

        assert_error_envelope(
            result,
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            severity="error",
        )

    def test_editor_response_missing_applied_is_rejected(self) -> None:
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
            "ops": [_valid_set_op("prefab")],
        }

        def respond(request: dict[str, object]) -> dict[str, object]:
            data = _success_data(request)
            del data["applied"]
            return {**_success_response(request), "data": data}

        with tempfile.TemporaryDirectory() as watch_dir:
            with EditorBridgeResponder(Path(watch_dir), respond):
                result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )

        assert_error_envelope(
            result,
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            severity="error",
        )

    def test_editor_response_contradictory_execution_states_are_rejected(self) -> None:
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
            "ops": [_valid_set_op("prefab")],
        }
        invalid_states: tuple[tuple[str, dict[str, object]], ...] = (
            ("read_only_true", {"read_only": True}),
            ("successful_executed_false", {"executed": False}),
            ("read_only_not_boolean", {"read_only": "false"}),
            ("executed_not_boolean", {"executed": "true"}),
        )

        def response_for(
            state: dict[str, object],
        ) -> Callable[[dict[str, object]], dict[str, object]]:
            def respond(request: dict[str, object]) -> dict[str, object]:
                return {
                    **_success_response(request),
                    "data": {**_success_data(request), **state},
                }

            return respond

        for label, invalid_state in invalid_states:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as watch_dir:
                with EditorBridgeResponder(
                    Path(watch_dir),
                    response_for(invalid_state),
                ):
                    result = _run_bridge(
                        payload,
                        env_overrides={
                            "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                            "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                        },
                    )

            assert_error_envelope(
                result,
                code="BRIDGE_UNITY_RESPONSE_SCHEMA",
                severity="error",
            )

    def test_editor_response_failed_state_contradictions_are_rejected(self) -> None:
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
            "ops": [_valid_set_op("prefab")],
        }

        def failed_response_for(
            response_severity: str,
            response_code: str,
        ) -> Callable[[dict[str, object]], dict[str, object]]:
            def failed_response(request: dict[str, object]) -> dict[str, object]:
                return {
                    **_success_response(request),
                    "success": False,
                    "severity": response_severity,
                    "code": response_code,
                }

            return failed_response

        contradictions = (
            ("info", "BRIDGE_RESOURCE_FAILED"),
            ("error", "SER_APPLY_OK"),
        )
        for severity, code in contradictions:
            with self.subTest(severity=severity, code=code):
                with tempfile.TemporaryDirectory() as watch_dir:
                    with EditorBridgeResponder(
                        Path(watch_dir),
                        failed_response_for(severity, code),
                    ):
                        result = _run_bridge(
                            payload,
                            env_overrides={
                                "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                                "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                            },
                        )

                assert_error_envelope(
                    result,
                    code="BRIDGE_UNITY_RESPONSE_SCHEMA",
                    severity="error",
                )

    def test_editor_response_successful_error_severities_are_rejected(self) -> None:
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
            "ops": [_valid_set_op("prefab")],
        }

        def successful_error_response_for(
            response_severity: str,
        ) -> Callable[[dict[str, object]], dict[str, object]]:
            def successful_error_response(
                request: dict[str, object],
            ) -> dict[str, object]:
                return {
                    **_success_response(request),
                    "severity": response_severity,
                }

            return successful_error_response

        for severity in ("error", "critical"):
            with self.subTest(severity=severity):
                with tempfile.TemporaryDirectory() as watch_dir:
                    with EditorBridgeResponder(
                        Path(watch_dir),
                        successful_error_response_for(severity),
                    ):
                        result = _run_bridge(
                            payload,
                            env_overrides={
                                "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                                "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                            },
                        )

                assert_error_envelope(
                    result,
                    code="BRIDGE_UNITY_RESPONSE_SCHEMA",
                    severity="error",
                )

    def test_multi_resource_boolean_applied_is_rejected_before_aggregation(
        self,
    ) -> None:
        payload = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "first",
                    "kind": "prefab",
                    "path": "Assets/First.prefab",
                    "mode": "open",
                },
                {
                    "id": "second",
                    "kind": "prefab",
                    "path": "Assets/Second.prefab",
                    "mode": "open",
                },
            ],
            "ops": [
                _valid_set_op("first"),
                _valid_set_op("second"),
            ],
        }
        with tempfile.TemporaryDirectory() as watch_dir:
            with EditorBridgeResponder(
                Path(watch_dir),
                lambda request: {
                    **_success_response(request),
                    "data": {**_success_data(request), "applied": True},
                },
            ):
                result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )

        assert_error_envelope(
            result,
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            severity="error",
        )

    def test_editor_response_request_metadata_is_canonicalized(self) -> None:
        payload = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "prefab",
                    "kind": "prefab",
                    "path": "Assets/Expected.prefab",
                    "mode": "open",
                }
            ],
            "ops": [_valid_set_op("prefab")],
        }
        created_result: dict[str, object] = {
            "handle": "created",
            "symbol_path": "Root/Created",
            "game_object_file_id": "1001",
            "transform_file_id": "1002",
            "source_asset_path": "Assets/Source.prefab",
            "source_asset_guid": "a" * 32,
            "overrides": [
                {
                    "component": "UnityEngine.Transform",
                    "property_path": "m_LocalPosition.x",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as watch_dir:
            with EditorBridgeResponder(
                Path(watch_dir),
                lambda request: {
                    **_success_response(request),
                    "data": {
                        **_success_data(request),
                        "target": "Assets/Other.prefab",
                        "op_count": -1,
                        "applied": 1,
                        "protocol_version": 999,
                        "created_results": [created_result],
                    },
                },
            ):
                result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )

        data = cast(dict[str, object], result["data"])
        self.assertIsInstance(data, dict)
        self.assertEqual(
            (
                True,
                "SER_APPLY_OK",
                "Assets/Expected.prefab",
                1,
                2,
                [created_result],
            ),
            (
                result["success"],
                result["code"],
                data["target"],
                data["op_count"],
                data["protocol_version"],
                data["created_results"],
            ),
        )

    def test_editor_response_invalid_data_metadata_is_rejected(self) -> None:
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
            "ops": [_valid_set_op("prefab")],
        }
        created_result = {
            "handle": "created",
            "symbol_path": "Root/Created",
            "game_object_file_id": "1001",
            "transform_file_id": "1002",
            "source_asset_path": "Assets/Source.prefab",
            "source_asset_guid": "a" * 32,
            "overrides": [
                {
                    "component": "UnityEngine.Transform",
                    "property_path": "m_LocalPosition.x",
                }
            ],
        }
        invalid_data: tuple[tuple[str, dict[str, object]], ...] = (
            ("negative_applied", {"applied": -1}),
            ("applied_exceeds_request", {"applied": 2}),
            (
                "unknown_data_field",
                {"applied": 1, "metadata": {"target": "/outside/hidden.prefab"}},
            ),
            (
                "unknown_created_result_field",
                {
                    "applied": 1,
                    "created_results": [{**created_result, "unknown": "value"}],
                },
            ),
            (
                "unknown_override_field",
                {
                    "applied": 1,
                    "created_results": [
                        {
                            **created_result,
                            "overrides": [
                                {
                                    "component": "UnityEngine.Transform",
                                    "property_path": "m_LocalPosition.x",
                                    "unknown": "value",
                                }
                            ],
                        }
                    ],
                },
            ),
        )

        def response_for(
            data: dict[str, object],
        ) -> Callable[[dict[str, object]], dict[str, object]]:
            def respond(request: dict[str, object]) -> dict[str, object]:
                return {
                    **_success_response(request),
                    "data": {**_success_data(request), **data},
                }

            return respond

        for label, data in invalid_data:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as watch_dir:
                with EditorBridgeResponder(Path(watch_dir), response_for(data)):
                    result = _run_bridge(
                        payload,
                        env_overrides={
                            "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                            "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                        },
                    )

            assert_error_envelope(
                result,
                code="BRIDGE_UNITY_RESPONSE_SCHEMA",
                severity="error",
            )

    def test_editor_response_rejects_incomplete_or_unknown_producer_fields(
        self,
    ) -> None:
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
            "ops": [_valid_set_op("prefab")],
        }

        def response_for(
            label: str,
        ) -> Callable[[dict[str, object]], dict[str, object]]:
            def respond(request: dict[str, object]) -> dict[str, object]:
                response = _success_response(request)
                if label == "missing_data_field":
                    data = _success_data(request)
                    del data["created_results"]
                    response["data"] = data
                elif label == "unknown_envelope_field":
                    response["unknown"] = {"target": "/outside/hidden.prefab"}
                else:
                    response["diagnostics"] = [
                        {
                            "path": "",
                            "location": "",
                            "detail": "",
                            "evidence": "",
                            "unknown": "value",
                        }
                    ]
                return response

            return respond

        for label in (
            "missing_data_field",
            "unknown_envelope_field",
            "unknown_diagnostic_field",
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as watch_dir:
                with EditorBridgeResponder(Path(watch_dir), response_for(label)):
                    result = _run_bridge(
                        payload,
                        env_overrides={
                            "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                            "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                        },
                    )

            assert_error_envelope(
                result,
                code="BRIDGE_UNITY_RESPONSE_SCHEMA",
                severity="error",
            )

    def test_multi_resource_applied_overflow_is_rejected_before_aggregation(
        self,
    ) -> None:
        payload = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "first",
                    "kind": "prefab",
                    "path": "Assets/First.prefab",
                    "mode": "open",
                },
                {
                    "id": "second",
                    "kind": "prefab",
                    "path": "Assets/Second.prefab",
                    "mode": "open",
                },
            ],
            "ops": [
                _valid_set_op("first"),
                _valid_set_op("second"),
            ],
        }

        def respond(request: dict[str, object]) -> dict[str, object]:
            applied = 2 if request["target"] == "Assets/Second.prefab" else 1
            return {
                **_success_response(request),
                "data": {**_success_data(request), "applied": applied},
            }

        with tempfile.TemporaryDirectory() as watch_dir:
            with EditorBridgeResponder(Path(watch_dir), respond):
                result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "10",
                    },
                )

        assert_error_envelope(
            result,
            code="BRIDGE_UNITY_RESPONSE_SCHEMA",
            severity="error",
        )

    def test_empty_operation_plan_is_rejected_before_editor_dispatch(self) -> None:
        with (
            tempfile.TemporaryDirectory() as watch_dir,
            patch(
                "tools.unity_patch_bridge._run_via_editor_bridge",
                return_value=_success_response({"target": "Assets/Test.prefab", "ops": []}),
            ) as mock_dispatch,
        ):
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
                env_overrides={"UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir},
            )

        assert_error_envelope(
            result,
            code="BRIDGE_REQUEST_SCHEMA",
            severity="error",
            data={
                "location": "ops",
                "error": "operations must be a non-empty array",
            },
        )
        mock_dispatch.assert_not_called()

    def test_invalid_resource_kind_and_mode_are_rejected_before_dispatch(self) -> None:
        invalid_fields: tuple[tuple[str, object], ...] = (
            ("kind", 123),
            ("kind", "texture"),
            ("mode", None),
            ("mode", "append"),
        )
        for field, value in invalid_fields:
            with (
                self.subTest(field=field, value=value),
                patch("tools.unity_patch_bridge._run_via_editor_bridge") as mock_dispatch,
            ):
                resource: dict[str, object] = {
                    "id": "prefab",
                    "kind": "prefab",
                    "path": "Assets/Test.prefab",
                    "mode": "open",
                }
                resource[field] = value
                result = _run_bridge(
                    {
                        "protocol_version": 2,
                        "plan_version": 2,
                        "resources": [resource],
                        "ops": [_valid_set_op("prefab")],
                    }
                )

                assert_error_envelope(
                    result,
                    code="BRIDGE_REQUEST_SCHEMA",
                    severity="error",
                )
                mock_dispatch.assert_not_called()

    def test_resource_without_operations_is_retained_in_metadata_only(self) -> None:
        with (
            tempfile.TemporaryDirectory() as watch_dir,
            patch(
                "tools.unity_patch_bridge._run_via_editor_bridge",
                return_value=_success_response({"target": "Assets/Used.prefab", "ops": [_valid_set_op("used")]}),
            ) as mock_dispatch,
        ):
            result = _run_bridge(
                {
                    "protocol_version": 2,
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "used",
                            "kind": "prefab",
                            "path": "Assets/Used.prefab",
                            "mode": "open",
                        },
                        {
                            "id": "unused",
                            "kind": "scene",
                            "path": "Assets/Unused.unity",
                            "mode": "open",
                        },
                    ],
                    "ops": [_valid_set_op("used")],
                },
                env_overrides={"UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir},
            )

        mock_dispatch.assert_called_once()
        dispatched = mock_dispatch.call_args.kwargs
        dispatched_resource = cast(dict[str, object], dispatched["resource"])
        dispatched_ops = cast(list[object], dispatched["ops"])
        result_data = cast(dict[str, object], result["data"])
        result_resources = cast(list[dict[str, object]], result_data["resources"])
        self.assertEqual(
            (
                True,
                "used",
                1,
                2,
                ["used", "unused"],
                [True, False],
                [1, 0],
            ),
            (
                result["success"],
                dispatched_resource["id"],
                len(dispatched_ops),
                result_data["resource_count"],
                [resource["id"] for resource in result_resources],
                [resource["executed"] for resource in result_resources],
                [resource["applied"] for resource in result_resources],
            ),
            msg="unused resources must remain metadata-only and never produce IPC requests",
        )
        self.assertEqual(
            {
                "id": "unused",
                "kind": "scene",
                "path": "Assets/Unused.unity",
                "mode": "open",
                "op_count": 0,
                "applied": 0,
                "executed": False,
            },
            result_resources[1],
        )


    def test_mixed_resource_response_round_trips_through_core_parser(self) -> None:
        ops: list[dict[str, Any]] = [_valid_set_op("used")]
        payload: dict[str, object] = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "used",
                    "kind": "prefab",
                    "path": "Assets/Used.prefab",
                    "mode": "open",
                },
                {
                    "id": "unused",
                    "kind": "scene",
                    "path": "Assets/Unused.unity",
                    "mode": "open",
                },
            ],
            "ops": ops,
        }
        producer_response = _success_response(
            {"target": "Assets/Used.prefab", "ops": ops}
        )

        with (
            tempfile.TemporaryDirectory() as watch_dir,
            patch(
                "tools.unity_patch_bridge._run_via_editor_bridge",
                return_value=producer_response,
            ) as mock_dispatch,
        ):
            aggregate = _run_bridge(
                payload,
                env_overrides={"UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir},
            )

        parsed = parse_bridge_response(
            aggregate,
            target_path=Path("Assets/Used.prefab"),
            ops=ops,
        )

        self.assertEqual(
            (True, "SER_APPLY_OK", aggregate["data"], 1),
            (
                parsed.success,
                parsed.code,
                parsed.data,
                mock_dispatch.call_count,
            ),
            msg="external aggregate responses must satisfy the core parser contract",
        )

        malformed = json.loads(json.dumps(aggregate))
        malformed_data = require_mapping(malformed["data"], "aggregate data")
        malformed_resources = cast(
            list[dict[str, object]],
            malformed_data["resources"],
        )
        malformed_resources[0]["kind"] = []
        rejected = parse_bridge_response(
            malformed,
            target_path=Path("Assets/Used.prefab"),
            ops=ops,
        )

        self.assertEqual(
            (False, "SER_BRIDGE_PROTOCOL"),
            (rejected.success, rejected.code),
            msg="malformed aggregate resource selectors must fail closed",
        )

    def test_core_parser_rejects_successful_error_severity_resource_summaries(
        self,
    ) -> None:
        ops: list[dict[str, Any]] = [_valid_set_op("used")]
        aggregate: dict[str, Any] = {
            "protocol_version": 2,
            "success": True,
            "severity": "info",
            "code": "SER_APPLY_OK",
            "message": "Bridge apply completed for all resources.",
            "data": {
                "plan_version": 2,
                "resource_count": 2,
                "op_count": 1,
                "applied": 1,
                "resources": [
                    {
                        "id": "used",
                        "kind": "prefab",
                        "path": "Assets/Used.prefab",
                        "mode": "open",
                        "op_count": 1,
                        "applied": 1,
                        "executed": True,
                        "success": True,
                        "severity": "info",
                        "code": "SER_APPLY_OK",
                    },
                    {
                        "id": "unused",
                        "kind": "scene",
                        "path": "Assets/Unused.unity",
                        "mode": "open",
                        "op_count": 0,
                        "applied": 0,
                        "executed": False,
                    },
                ],
                "read_only": False,
                "executed": True,
                "protocol_version": 2,
            },
            "diagnostics": [],
        }

        for severity in ("error", "critical"):
            with self.subTest(severity=severity):
                malformed: Any = json.loads(json.dumps(aggregate))
                malformed["data"]["resources"][0]["severity"] = severity
                response = parse_bridge_response(
                    malformed,
                    target_path=Path("Assets/Used.prefab"),
                    ops=ops,
                )

                assert_error_envelope(
                    response,
                    code="SER_BRIDGE_PROTOCOL",
                    severity="error",
                    message_match=r"^Unity bridge response schema is invalid\.$",
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

    def test_insert_array_element_without_value_is_rejected_with_op_location_pinned(
        self,
    ) -> None:
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
                        "op": "insert_array_element",
                        "component": "Example.Component",
                        "path": "items.Array.data",
                        "index": 0,
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
                "error": "insert_array_element operation requires 'value'",
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
                        "mode": "create",
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
                "ops": [_valid_set_op("asset")],
            }
        )
        assert_error_envelope(
            result,
            code="BRIDGE_UNSUPPORTED_TARGET",
            severity="error",
        )

    def test_single_resource_round_trip_preserves_exact_producer_response(self) -> None:
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
            (
                "SER_APPLY_OK",
                {
                    "target": "Assets/Test.prefab",
                    "op_count": 1,
                    "applied": 1,
                    "read_only": False,
                    "executed": True,
                    "protocol_version": 2,
                    "created_results": [],
                },
            ),
            (result["code"], data),
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
                "ops": [_valid_set_op("prefab")],
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
        response = _routing_faithful_response({"protocol_version": 2, "target": "Assets/Test.prefab", "ops": []})
        self.assertEqual(
            (False, "EDITOR_BRIDGE_UNKNOWN_ACTION"),
            (bool(response["success"]), response["code"]),
            msg=(f"an action-less request must be rejected with EDITOR_BRIDGE_UNKNOWN_ACTION; got {response!r}"),
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
            msg=("the patch_apply discriminator collides with the editor-control action set; pick a value outside it."),
        )


class CoreBridgeCreatedResultContractTests(unittest.TestCase):
    @staticmethod
    def _instantiate_op(handle: str) -> dict[str, object]:
        return {
            "resource": "target",
            "op": "instantiate_prefab",
            "prefab": "Assets/Source.prefab",
            "parent": "$root",
            "result": handle,
        }

    @staticmethod
    def _created_result(handle: str) -> dict[str, object]:
        return {
            "handle": handle,
            "symbol_path": f"Root/{handle}",
            "game_object_file_id": "1001",
            "transform_file_id": "1002",
            "source_asset_path": "Assets/Source.prefab",
            "source_asset_guid": "a" * 32,
            "overrides": [],
        }

    def _parse_success(
        self,
        ops: list[dict[str, object]],
        created_results: list[dict[str, object]],
        *,
        applied: int | None = None,
    ):
        request: dict[str, object] = {
            "target": "Assets/Target.prefab",
            "ops": ops,
        }
        payload = _success_response(request)
        data = cast(dict[str, object], payload["data"])
        data["created_results"] = created_results
        if applied is not None:
            data["applied"] = applied
        return parse_bridge_response(
            payload,
            target_path=Path("Assets/Target.prefab"),
            ops=cast(list[dict[str, Any]], ops),
            resource_kind="prefab",
            resource_mode="open",
        )

    def test_success_requires_every_operation_to_be_applied(self) -> None:
        response = self._parse_success(
            [_valid_set_op("target")],
            [],
            applied=0,
        )

        assert_error_envelope(
            response,
            code="SER_BRIDGE_PROTOCOL",
            severity="error",
        )

    def test_success_requires_exact_instantiated_result_handle_set(self) -> None:
        ops = [
            self._instantiate_op("$first"),
            self._instantiate_op("second"),
        ]
        cases = (
            ("missing", [self._created_result("first")]),
            (
                "unexpected",
                [
                    self._created_result("first"),
                    self._created_result("unexpected"),
                ],
            ),
        )

        for label, created_results in cases:
            with self.subTest(label=label):
                response = self._parse_success(ops, created_results)

                assert_error_envelope(
                    response,
                    code="SER_BRIDGE_PROTOCOL",
                    severity="error",
                )

    def test_created_result_handles_must_be_unique(self) -> None:
        ops = [
            self._instantiate_op("first"),
            self._instantiate_op("second"),
        ]

        response = self._parse_success(
            ops,
            [
                self._created_result("first"),
                self._created_result("second"),
                self._created_result("first"),
            ],
        )

        assert_error_envelope(
            response,
            code="SER_BRIDGE_PROTOCOL",
            severity="error",
        )

    def test_created_result_identity_fields_must_be_non_empty(self) -> None:
        ops = [self._instantiate_op("nested")]
        required_fields = (
            "handle",
            "symbol_path",
            "game_object_file_id",
            "transform_file_id",
            "source_asset_path",
            "source_asset_guid",
        )

        for field in required_fields:
            with self.subTest(field=field):
                created_result = self._created_result("nested")
                created_result[field] = "   "
                response = self._parse_success(ops, [created_result])

                assert_error_envelope(
                    response,
                    code="SER_BRIDGE_PROTOCOL",
                    severity="error",
                )

    def test_success_accepts_complete_created_result_contract(self) -> None:
        response = self._parse_success(
            [self._instantiate_op("$nested")],
            [self._created_result("nested")],
        )

        self.assertEqual(
            (True, "SER_APPLY_OK", ["nested"]),
            (
                response.success,
                response.code,
                [item["handle"] for item in response.data["created_results"]],
            ),
            msg=f"unexpected complete bridge contract response: {response.to_dict()!r}",
        )


class OpenPrefabCompositionBridgeTests(unittest.TestCase):
    def test_composable_handles_reach_one_open_prefab_apply_request(self) -> None:
        captured: list[dict[str, object]] = []

        def responder(request: dict[str, object]) -> dict[str, object]:
            captured.append(request)
            return _success_response(request)

        payload = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "target",
                    "kind": "prefab",
                    "path": "Assets/Target.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {
                    "resource": "target",
                    "op": "instantiate_prefab",
                    "prefab": "Assets/Source.prefab",
                    "parent": "$root",
                    "result": "nested",
                },
                {
                    "resource": "target",
                    "op": "rename_object",
                    "target": "$nested",
                    "name": "Nested B",
                },
                {
                    "resource": "target",
                    "op": "find_game_object",
                    "target": "$nested",
                    "relative_symbol_path": "Screens/Output#2",
                    "result": "screen",
                },
                {
                    "resource": "target",
                    "op": "find_component",
                    "target": "$screen",
                    "type": "Example.ScreenTarget",
                    "result": "screen_component",
                },
                {
                    "resource": "target",
                    "op": "find_game_object",
                    "symbol_path": "Existing/Controller",
                    "result": "controller",
                },
                {
                    "resource": "target",
                    "op": "find_component",
                    "target": "$controller",
                    "type": "Example.Controller",
                    "result": "controller_component",
                },
                {
                    "resource": "target",
                    "op": "set",
                    "target": "$controller_component",
                    "path": "m_Target",
                    "value": {"handle": "$screen_component"},
                },
            ],
        }

        with tempfile.TemporaryDirectory() as watch_dir:
            with EditorBridgeResponder(Path(watch_dir), responder):
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
            msg=f"unexpected bridge envelope: {result!r}",
        )
        self.assertEqual(1, len(captured))
        request = captured[0]
        self.assertEqual(
            ("patch_apply", "Assets/Target.prefab", "prefab", "open"),
            (
                request["action"],
                request["target"],
                request["kind"],
                request["mode"],
            ),
        )
        self.assertIsInstance(request["ops"], list)
        request_ops = cast(list[dict[str, object]], request["ops"])
        self.assertEqual(
            [
                "instantiate_prefab",
                "rename_object",
                "find_game_object",
                "find_component",
                "find_game_object",
                "find_component",
                "set",
            ],
            [op["op"] for op in request_ops],
        )
        self.assertEqual(
            {
                "op": "find_game_object",
                "target": "$nested",
                "relative_symbol_path": "Screens/Output#2",
                "result": "screen",
            },
            request_ops[2],
        )
        self.assertEqual(
            {
                "op": "find_game_object",
                "symbol_path": "Existing/Controller",
                "result": "controller",
            },
            request_ops[4],
        )
        self.assertEqual(
            {
                "op": "set",
                "path": "m_Target",
                "target": "$controller_component",
                "value_kind": "handle",
                "value_string": "$screen_component",
            },
            request_ops[6],
        )
        self.assertEqual(
            [],
            [op["op"] for op in request_ops if op["op"] in {"save", "wire_reference"}],
        )


class EditorBridgeModeTests(unittest.TestCase):
    """Pin the editor-bridge file-watcher dispatch envelopes."""

    INVALID_WATCH_DIR_MESSAGE = "UNITYTOOL_BRIDGE_WATCH_DIR must name an existing Editor Bridge watch directory."

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
                "ops": [_valid_set_op("prefab")],
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

    def test_missing_watch_dir_is_rejected_without_creating_transport_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            watch_dir = Path(temp_dir) / "missing-watch"
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
                    "ops": [_valid_set_op("prefab")],
                },
                env_overrides={
                    "UNITYTOOL_BRIDGE_WATCH_DIR": str(watch_dir),
                    "UNITYTOOL_UNITY_TIMEOUT_SEC": "1",
                },
            )

            self.assertEqual(
                (
                    True,
                    False,
                    "error",
                    "BRIDGE_WATCH_DIR_MISSING",
                    self.INVALID_WATCH_DIR_MESSAGE,
                    False,
                ),
                (
                    exit_code != 0,
                    result["success"],
                    result["severity"],
                    result["code"],
                    result["message"],
                    watch_dir.exists(),
                ),
                msg=f"missing watch dir must fail fast without creating transport state: {result!r}",
            )

    def test_non_directory_watch_path_is_rejected_before_request_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            watch_path = Path(temp_dir) / "watch-file"
            watch_path.write_text("not a directory", encoding="utf-8")
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
                    "ops": [_valid_set_op("prefab")],
                },
                env_overrides={
                    "UNITYTOOL_BRIDGE_WATCH_DIR": str(watch_path),
                    "UNITYTOOL_UNITY_TIMEOUT_SEC": "1",
                },
            )

            self.assertEqual(
                (
                    False,
                    "error",
                    "BRIDGE_WATCH_DIR_MISSING",
                    self.INVALID_WATCH_DIR_MESSAGE,
                    "not a directory",
                ),
                (
                    result["success"],
                    result["severity"],
                    result["code"],
                    result["message"],
                    watch_path.read_text(encoding="utf-8"),
                ),
                msg=f"non-directory watch path must fail before request mutation: {result!r}",
            )

    def test_watch_dir_status_failure_returns_stable_missing_envelope(self) -> None:
        secret = "/secret/watch-status"
        with (
            tempfile.TemporaryDirectory() as watch_dir,
            patch.object(
                Path,
                "is_dir",
                side_effect=OSError(secret),
            ),
        ):
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
                    "ops": [_valid_set_op("prefab")],
                },
                env_overrides={
                    "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                    "UNITYTOOL_UNITY_TIMEOUT_SEC": "1",
                },
            )

        serialized = json.dumps(result)
        self.assertEqual(
            (
                False,
                "error",
                "BRIDGE_WATCH_DIR_MISSING",
                self.INVALID_WATCH_DIR_MESSAGE,
                False,
                False,
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                secret in serialized,
                watch_dir in serialized,
            ),
            msg=f"watch-dir status failure must return a stable redacted envelope: {result!r}",
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
                    "ops": [_valid_set_op("prefab")],
                },
                env_overrides={
                    "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                    "UNITYTOOL_UNITY_TIMEOUT_SEC": "2",
                },
            )
        self.assertNotIn(watch_dir, json.dumps(result))
        self.assertEqual(
            "BRIDGE_EDITOR_TIMEOUT",
            result["code"],
            msg=(f"an empty watch dir with no responder must produce a BRIDGE_EDITOR_TIMEOUT envelope; got {result!r}"),
        )

    def test_file_ipc_failures_do_not_expose_exception_or_transport_paths(self) -> None:
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
            "ops": [_valid_set_op("prefab")],
        }
        original_write_text = Path.write_text
        original_exists = Path.exists
        original_read_text = Path.read_text

        with tempfile.TemporaryDirectory() as watch_dir:

            def fail_request_write(
                path: Path,
                data: str,
                encoding: str | None = None,
                errors: str | None = None,
                newline: str | None = None,
            ) -> int:
                if path.name.endswith(".request.json.tmp"):
                    raise OSError("/secret/request-write")
                return original_write_text(
                    path,
                    data,
                    encoding=encoding,
                    errors=errors,
                    newline=newline,
                )

            with patch.object(Path, "write_text", fail_request_write):
                write_result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "2",
                    },
                )

            def fail_response_status(path: Path) -> bool:
                if path.name.endswith(".response.json"):
                    raise OSError("/secret/response-status")
                return original_exists(path)

            with patch.object(Path, "exists", fail_response_status):
                status_result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "2",
                    },
                )

            def response_ready(path: Path) -> bool:
                if path.name.endswith(".response.json"):
                    return True
                return original_exists(path)

            def fail_response_read(
                path: Path,
                encoding: str | None = None,
                errors: str | None = None,
            ) -> str:
                if path.name.endswith(".response.json"):
                    raise OSError("/secret/response-read")
                return original_read_text(path, encoding=encoding, errors=errors)

            with (
                patch.object(Path, "exists", response_ready),
                patch.object(Path, "read_text", fail_response_read),
            ):
                read_result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "2",
                    },
                )

        self.assertEqual(
            (
                "BRIDGE_EDITOR_WRITE",
                "BRIDGE_EDITOR_RESPONSE_READ",
                "BRIDGE_EDITOR_RESPONSE_READ",
            ),
            (
                write_result["code"],
                status_result["code"],
                read_result["code"],
            ),
        )
        serialized = json.dumps((write_result, status_result, read_result))
        self.assertNotIn("/secret/", serialized)
        self.assertNotIn(watch_dir, serialized)

    def test_invalid_utf8_response_is_redacted_and_transport_files_are_cleaned(self) -> None:
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
            "ops": [_valid_set_op("prefab")],
        }
        original_exists = Path.exists
        original_read_text = Path.read_text

        with tempfile.TemporaryDirectory() as watch_dir:

            def response_ready(path: Path) -> bool:
                if path.name.endswith(".response.json"):
                    return True
                return original_exists(path)

            def fail_response_decode(
                path: Path,
                encoding: str | None = None,
                errors: str | None = None,
            ) -> str:
                if path.name.endswith(".response.json"):
                    raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
                return original_read_text(path, encoding=encoding, errors=errors)

            with (
                patch.object(Path, "exists", response_ready),
                patch.object(Path, "read_text", fail_response_decode),
            ):
                result = _run_bridge(
                    payload,
                    env_overrides={
                        "UNITYTOOL_BRIDGE_WATCH_DIR": watch_dir,
                        "UNITYTOOL_UNITY_TIMEOUT_SEC": "2",
                    },
                )

            remaining_files = list(Path(watch_dir).iterdir())

        self.assertEqual(
            (False, "BRIDGE_EDITOR_RESPONSE_READ", []),
            (result["success"], result["code"], remaining_files),
            msg=f"invalid UTF-8 must use the stable read envelope and clean IPC files: {result!r}",
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
                        "ops": [_valid_set_op("prefab")],
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
                responder.observed_requests[0]["target"] if responder.observed_requests else None,
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
                    "ops": [_valid_set_op("prefab")],
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
        accept_payload = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "prefab",
                    "kind": "prefab",
                    "path": "Assets/Test.prefab",
                    "mode": "create",
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
                    "kind": "prefab",
                    "path": "Assets/Test.prefab",
                    "mode": "create",
                }
            ],
            "ops": [
                {"resource": "prefab", "op": "remove_component"},
            ],
        }

        with self.subTest(branch="accept_component_selector"):
            result = _run_bridge(accept_payload)
            assert_error_envelope(
                result,
                code="BRIDGE_WATCH_DIR_MISSING",
                severity="error",
            )

        with self.subTest(branch="reject_when_target_and_component_missing"):
            result = _run_bridge(reject_payload)
            assert_error_envelope(
                result,
                code="BRIDGE_REQUEST_SCHEMA",
                severity="error",
                data={
                    "location": "ops[0]",
                    "error": (
                        "remove_component requires a non-empty 'target' or 'component'"
                    ),
                },
            )

    def test_open_prefab_validator_rejects_every_unsupported_bridge_operation(
        self,
    ) -> None:
        from tools.unity_patch_bridge import SUPPORTED_OP_NAMES

        bridge_open_ops = {
            "instantiate_prefab",
            "rename_object",
            "find_game_object",
            "find_component",
            "set",
            "insert_array_element",
            "remove_array_element",
        }
        forbidden_ops = sorted(SUPPORTED_OP_NAMES - bridge_open_ops)

        for op_name in forbidden_ops:
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
                "ops": [{"resource": "prefab", "op": op_name}],
            }

            with self.subTest(op=op_name):
                result = _run_bridge(payload)
                assert_error_envelope(
                    result,
                    code="BRIDGE_REQUEST_SCHEMA",
                    severity="error",
                    data={
                        "location": "ops[0].op",
                        "error": "open prefab operation is unsupported",
                    },
                )

    def test_prefab_create_mode_retains_generic_add_component_validation(self) -> None:
        payload = {
            "protocol_version": 2,
            "plan_version": 2,
            "resources": [
                {
                    "id": "prefab",
                    "kind": "prefab",
                    "path": "Assets/Test.prefab",
                    "mode": "create",
                }
            ],
            "ops": [
                {
                    "resource": "prefab",
                    "op": "add_component",
                    "target": "/CharacterBody",
                    "type": "AudioSource",
                }
            ],
        }

        result = _run_bridge(payload)

        assert_error_envelope(
            result,
            code="BRIDGE_WATCH_DIR_MISSING",
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
                "ops": [_valid_set_op("prefab")],
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
            msg=(f"a file_id-targeted set op must pass schema validation; got exit={exit_code!r} envelope={parsed!r}"),
        )
        observed_op = responder.observed_requests[0]["ops"][0]
        self.assertEqual(
            "300",
            observed_op.get("file_id"),
            msg=("the bridge wire encoder must forward op.file_id to the editor-bridge request (issue #37)."),
        )

    def test_array_ops_file_id_target_survive_to_the_bridge_request(self) -> None:
        """Exact file IDs must survive host validation and wire normalization."""
        cases: tuple[tuple[str, dict[str, Any]], ...] = (
            ("insert_array_element", {"value": "probe"}),
            ("remove_array_element", {}),
        )
        for op_name, extra_fields in cases:
            with self.subTest(op=op_name), tempfile.TemporaryDirectory() as watch_dir:
                watch_path = Path(watch_dir)
                operation = {
                    "resource": "prefab",
                    "op": op_name,
                    "file_id": "300",
                    "path": "items.Array.data",
                    "index": 0,
                    **extra_fields,
                }
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
                    "ops": [operation],
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
                        f"a file_id-targeted {op_name} op must pass schema "
                        f"validation; got exit={exit_code!r} envelope={parsed!r}"
                    ),
                )
                self.assertEqual(1, len(responder.observed_requests))
                observed_op = responder.observed_requests[0]["ops"][0]
                self.assertEqual(
                    (op_name, "300", 0),
                    (
                        observed_op.get("op"),
                        observed_op.get("file_id"),
                        observed_op.get("index"),
                    ),
                )
                if op_name == "insert_array_element":
                    self.assertEqual(
                        ("string", "probe"),
                        (
                            observed_op.get("value_kind"),
                            observed_op.get("value_string"),
                        ),
                    )

    def test_malformed_stdin_exits_nonzero_with_request_json_failure_envelope(self) -> None:
        captured = io.StringIO()
        with redirect_stdout(captured):
            exit_code = _bridge_main(stdin=io.StringIO("not-json"))
        parsed = json.loads(captured.getvalue())

        self.assertEqual(
            (True, False, "BRIDGE_REQUEST_JSON", {}),
            (
                exit_code != 0,
                bool(parsed["success"]),
                parsed["code"],
                parsed["data"],
            ),
            msg=(
                "malformed stdin must produce a sanitized non-zero "
                f"BRIDGE_REQUEST_JSON envelope; got exit={exit_code!r} "
                f"envelope={parsed!r}"
            ),
        )

    def test_invalid_plan_schema_does_not_echo_caller_controlled_values(self) -> None:
        secret = "SECRET_PATH"
        captured = io.StringIO()
        request = {
            "protocol_version": 2,
            "plan_version": secret,
            "resources": [],
        }

        with redirect_stdout(captured):
            exit_code = _bridge_main(stdin=io.StringIO(json.dumps(request)))
        parsed = json.loads(captured.getvalue())

        self.assertEqual(
            (
                True,
                False,
                "BRIDGE_REQUEST_SCHEMA",
                "Bridge request schema is invalid.",
                False,
            ),
            (
                exit_code != 0,
                bool(parsed["success"]),
                parsed["code"],
                parsed["message"],
                secret in json.dumps(parsed),
            ),
            msg=(
                "plan normalization failures must not echo caller-controlled "
                f"values: exit={exit_code!r} envelope={parsed!r}"
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
