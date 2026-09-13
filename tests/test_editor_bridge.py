"""Tests for prefab_sentinel.editor_bridge module."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from prefab_sentinel.editor_bridge import (
    BRIDGE_TIMEOUT_ENV,
    BRIDGE_WATCH_DIR_ENV,
    PROTOCOL_VERSION,
    SUPPORTED_ACTIONS,
    bridge_status,
    check_editor_bridge_env,
    get_last_bridge_version,
    send_action,
    send_private_acceptance_action,
)
from prefab_sentinel.editor_bridge_builders import build_create_empty_kwargs, build_set_camera_kwargs
from prefab_sentinel.unity_assets_path import resolve_asset_path
from tests._typing_helpers import require_mapping, require_not_none


class TestCheckEditorBridgeEnv(unittest.TestCase):
    """Tests for the watch-directory-only env validator (issue #270)."""

    def test_watch_dir_present_returns_no_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = check_editor_bridge_env()
                self.assertIsNone(result)

    def test_watch_dir_unset_emits_watch_dir_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = require_not_none(check_editor_bridge_env(), "watch-dir missing envelope")
        self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_MISSING", result["code"])
        self.assertEqual("watch_dir", result["data"]["blocker_class"])
        self.assertEqual(
            "Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory.",
            result["data"]["suggested_next_action"],
        )

    def test_watch_dir_nonexistent_emits_watch_dir_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "secret-missing"
            with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: str(missing)}):
                result = require_not_none(check_editor_bridge_env(), "watch-dir missing-path envelope")

        self.assertEqual(
            (
                "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
                {
                    "env_var": BRIDGE_WATCH_DIR_ENV,
                    "blocker_class": "watch_dir",
                    "suggested_next_action": (
                        "Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory."
                    ),
                },
                False,
            ),
            (
                result["code"],
                result["data"],
                str(missing) in json.dumps(result),
            ),
            msg=f"missing watch-dir failures must not expose the configured path: {result!r}",
        )

    def test_watch_dir_status_probe_error_emits_watch_dir_not_found(self) -> None:
        secret = "ISSUE162_SECRET_PROBE_ERROR"
        with tempfile.TemporaryDirectory() as tmp:
            watch_dir = Path(tmp)
            original_is_dir = Path.is_dir

            def fail_watch_dir_probe(path: Path) -> bool:
                if path == watch_dir:
                    raise OSError(secret)
                return original_is_dir(path)

            try:
                Path.is_dir = fail_watch_dir_probe  # type: ignore[assignment]
                with (
                    patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: str(watch_dir)}),
                    self.assertLogs("prefab_sentinel.editor_bridge", level="ERROR") as captured,
                ):
                    result = require_not_none(
                        check_editor_bridge_env(),
                        "watch-dir status-error envelope",
                    )
            finally:
                Path.is_dir = original_is_dir  # type: ignore[assignment]

        public_wire = json.dumps(result)
        private_log = "\n".join(captured.output)
        self.assertEqual(
            (
                "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
                {
                    "env_var": BRIDGE_WATCH_DIR_ENV,
                    "blocker_class": "watch_dir",
                    "suggested_next_action": (
                        "Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory."
                    ),
                },
                False,
                False,
                True,
                True,
            ),
            (
                result["code"],
                result["data"],
                secret in public_wire,
                str(watch_dir) in public_wire,
                secret in private_log,
                str(watch_dir) in private_log,
            ),
            msg=f"public status must redact transport details while private logs retain them: {result!r}",
        )

    def test_bridge_status_probe_error_reports_disconnected(self) -> None:
        secret = "ISSUE162_SECRET_BRIDGE_STATUS"
        with tempfile.TemporaryDirectory() as tmp:
            watch_dir = Path(tmp)
            original_is_dir = Path.is_dir

            def fail_watch_dir_probe(path: Path) -> bool:
                if path == watch_dir:
                    raise OSError(secret)
                return original_is_dir(path)

            try:
                Path.is_dir = fail_watch_dir_probe  # type: ignore[assignment]
                with (
                    patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: str(watch_dir)}),
                    self.assertLogs("prefab_sentinel.editor_bridge", level="ERROR") as captured,
                ):
                    status = bridge_status()
            finally:
                Path.is_dir = original_is_dir  # type: ignore[assignment]

        public_wire = json.dumps(status)
        private_log = "\n".join(captured.output)
        self.assertEqual(
            {
                "connected": False,
                "connection_state": "unavailable",
                "code": "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
                "blocker_class": "watch_dir",
                "suggested_next_action": (
                    "Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory."
                ),
            },
            status,
        )
        self.assertNotIn(secret, public_wire)
        self.assertNotIn(str(watch_dir), public_wire)
        self.assertIn(secret, private_log)
        self.assertIn(str(watch_dir), private_log)

    def test_wsl_conversion_applied_in_check_editor_bridge_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: "D:\\Project\\Watch"},
                clear=False,
            ):
                with patch("prefab_sentinel.editor_bridge.to_wsl_path", return_value=tmpdir) as mock_to_wsl_path:
                    result = check_editor_bridge_env()
                    self.assertIsNone(result)
                    mock_to_wsl_path.assert_called_with("D:\\Project\\Watch")

    def test_wsl_conversion_applied_in_bridge_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: "D:\\Project\\Watch"},
                clear=False,
            ):
                with patch("prefab_sentinel.editor_bridge.to_wsl_path", return_value=tmpdir) as mock_to_wsl_path:
                    result = bridge_status()
                    self.assertEqual(
                        {
                            "connected": True,
                            "connection_state": "connected",
                            "code": None,
                            "blocker_class": None,
                            "suggested_next_action": None,
                        },
                        result,
                    )
                    mock_to_wsl_path.assert_called_with("D:\\Project\\Watch")

    def test_bridge_status_uses_captured_watch_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {}, clear=True):
                result = bridge_status(Path(tmpdir))

        self.assertEqual(
            {
                "connected": True,
                "connection_state": "connected",
                "code": None,
                "blocker_class": None,
                "suggested_next_action": None,
            },
            result,
        )

    def test_wsl_conversion_applied_in_send_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: "D:\\Project\\Watch"},
                clear=False,
            ):
                with patch("prefab_sentinel.editor_bridge.check_editor_bridge_env", return_value=None):
                    with patch("prefab_sentinel.editor_bridge.to_wsl_path", return_value=tmpdir) as mock_to_wsl_path:
                        # timeout_sec=1 keeps the call short (no responder)
                        # while staying above the > 0 boundary check.
                        send_action(action="capture_screenshot", timeout_sec=1)
                        mock_to_wsl_path.assert_called_once_with("D:\\Project\\Watch")

    def test_windows_path_without_wsl_conversion_fails(self) -> None:
        with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: "Z:\\Missing\\Watch"}):
            result = require_not_none(check_editor_bridge_env(), "windows watch-dir envelope")
        self.assertEqual(
            (False, "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND"),
            (result["success"], result["code"]),
        )


class TestSendAction(unittest.TestCase):
    """Tests for send_action request/response protocol."""

    def test_unknown_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = send_action(action="nonexistent_action")
                self.assertFalse(result["success"])
                self.assertEqual("EDITOR_BRIDGE_UNKNOWN_ACTION", result["code"])

    def test_env_not_configured(self) -> None:
        with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: ""}, clear=False):
            result = send_action(action="capture_screenshot")
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_MISSING", result["code"])

    def test_explicit_watch_directory_does_not_require_process_environment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: ""},
                clear=False,
            ):
                result = send_private_acceptance_action(
                    action="not-a-private-action",
                    watch_dir=Path(tmpdir),
                )

        self.assertEqual("EDITOR_BRIDGE_UNKNOWN_ACTION", result["code"])

    def test_zero_timeout_sec_rejected_at_boundary(self) -> None:
        """``timeout_sec <= 0`` is rejected with a dedicated envelope
        before any IPC write, mirroring the runtime-bridge invoke path.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = send_action(action="capture_screenshot", timeout_sec=0)
                self.assertEqual("EDITOR_BRIDGE_TIMEOUT_INVALID", result["code"])
                self.assertEqual(0, result["data"]["received_timeout"])

    def test_non_numeric_timeout_env_returns_invalid_envelope_before_write(self) -> None:
        received_timeout = "not-an-int"
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.dict(
                    os.environ,
                    {
                        BRIDGE_WATCH_DIR_ENV: tmpdir,
                        BRIDGE_TIMEOUT_ENV: received_timeout,
                    },
                    clear=False,
                ),
                patch.object(Path, "write_text") as write_text,
            ):
                result = send_action(action="capture_screenshot")

        self.assertEqual(
            (
                False,
                "EDITOR_BRIDGE_TIMEOUT_INVALID",
                {"received_timeout": received_timeout},
            ),
            (result["success"], result["code"], result["data"]),
            msg=f"invalid timeout configuration must return a stable envelope: {result!r}",
        )
        write_text.assert_not_called()

    def test_negative_timeout_sec_rejected_at_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = send_action(action="capture_screenshot", timeout_sec=-1)
                self.assertEqual("EDITOR_BRIDGE_TIMEOUT_INVALID", result["code"])

    def test_request_file_written_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {BRIDGE_WATCH_DIR_ENV: tmpdir},
                clear=False,
            ):
                result = send_action(
                    action="capture_screenshot",
                    view="scene",
                    width=800,
                    height=600,
                    timeout_sec=1,
                )

        self.assertEqual(
            (
                False,
                "EDITOR_BRIDGE_TIMEOUT",
                {
                    "action": "capture_screenshot",
                    "timeout_sec": 1,
                    "blocker_class": "bridge_connection",
                    "suggested_next_action": (
                        "Confirm Unity is running and the PrefabSentinel Editor Bridge watcher is active."
                    ),
                },
                False,
            ),
            (
                result["success"],
                result["code"],
                result["data"],
                tmpdir in json.dumps(result),
            ),
            msg=f"timeout failures must retain safe context without request paths: {result!r}",
        )


    def test_publication_failure_marker_returns_typed_failure_and_cleans_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir)
            responder_errors: list[BaseException] = []

            import threading

            request_ready = threading.Condition()
            observed_request: dict[str, Path] = {}
            original_rename = Path.rename

            def notifying_rename(self_path: Path, target: str | Path) -> Path:
                renamed_path = original_rename(self_path, target)
                target_path = Path(target)
                if (
                    target_path.parent == watch_dir
                    and target_path.name.endswith(".request.json")
                ):
                    with request_ready:
                        observed_request["path"] = target_path
                        request_ready.notify_all()
                return renamed_path

            def fail_publication() -> None:
                try:
                    with request_ready:
                        request_seen = request_ready.wait_for(
                            lambda: "path" in observed_request,
                            timeout=2,
                        )
                    if not request_seen:
                        raise AssertionError(
                            "Expected request file before publication failure"
                        )

                    request_file = observed_request["path"]
                    request_id = request_file.name.removesuffix(".request.json")
                    response_tmp = watch_dir / f"{request_id}.response.json.tmp"
                    failure_marker = (
                        watch_dir / f"{request_id}.publication-failed.json"
                    )
                    response_tmp.write_text("PRIVATE_PARTIAL_RESPONSE", encoding="utf-8")
                    original_rename(request_file, failure_marker)
                except BaseException as exc:
                    responder_errors.append(exc)

            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: tmpdir},
                    clear=False,
                ),
                patch.object(Path, "rename", notifying_rename),
            ):
                thread = threading.Thread(target=fail_publication)
                thread.start()
                result = send_action(
                    action="capture_screenshot",
                    timeout_sec=2,
                )
                thread.join()

                if responder_errors:
                    raise responder_errors[0]

                self.assertEqual([], responder_errors)
                self.assertEqual(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "success": False,
                        "severity": "error",
                        "code": "EDITOR_BRIDGE_RESPONSE_PUBLICATION_FAILED",
                        "message": (
                            "Editor Bridge processed the request but could not "
                            "publish its response."
                        ),
                        "data": {
                            "action": "capture_screenshot",
                            "state_unknown": True,
                        },
                        "diagnostics": [],
                    },
                    result,
                )
                self.assertEqual([], list(watch_dir.iterdir()))
                self.assertNotIn(tmpdir, json.dumps(result))
                self.assertNotIn("PRIVATE_PARTIAL_RESPONSE", json.dumps(result))

    def test_deleted_watch_directory_after_preflight_is_not_recreated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            watch_dir = Path(temp_dir) / "watch"
            watch_dir.mkdir()
            check_count = 0

            def validate_then_delete(
                captured_watch_dir: Path | None | object,
            ) -> dict[str, object] | None:
                nonlocal check_count
                result = check_editor_bridge_env(captured_watch_dir)
                if check_count == 0:
                    watch_dir.rmdir()
                check_count += 1
                return result

            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: str(watch_dir)},
                    clear=False,
                ),
                patch(
                    "prefab_sentinel.editor_bridge.check_editor_bridge_env",
                    side_effect=validate_then_delete,
                ),
            ):
                result = send_action(action="capture_screenshot", timeout_sec=1)

        self.assertEqual(
            (
                False,
                "error",
                "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
                (
                    "Editor Bridge watch directory does not exist. "
                    "Set UNITYTOOL_BRIDGE_WATCH_DIR=<path>. "
                    "See README 'Unity Bridge セットアップ' section."
                ),
                2,
                False,
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                check_count,
                watch_dir.exists(),
            ),
            msg=(
                "a vanished resident watch directory must fail before request "
                f"write without being recreated: {result!r}"
            ),
        )

    def test_response_poll_exists_failure_returns_envelope_and_cleans_request(self) -> None:
        secret = "ISSUE162_SECRET_RESPONSE_STATUS"
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir)
            original_exists = Path.exists

            def fail_response_exists(path: Path) -> bool:
                if path.parent == watch_dir and path.name.endswith(".response.json"):
                    raise OSError(secret)
                return original_exists(path)

            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: tmpdir},
                    clear=False,
                ),
                patch.object(Path, "exists", fail_response_exists),
                self.assertLogs("prefab_sentinel.editor_bridge", level="ERROR") as captured,
            ):
                result = send_action(
                    action="capture_screenshot",
                    view="scene",
                    width=1,
                    height=1,
                    timeout_sec=1,
                )
            remaining_requests = [
                path.name
                for path in watch_dir.iterdir()
                if path.name.endswith(".request.json")
            ]

        public_wire = json.dumps(result)
        private_log = "\n".join(captured.output)
        self.assertEqual(
            (
                False,
                "EDITOR_BRIDGE_RESPONSE_READ",
                {},
                [],
                False,
                False,
                True,
                True,
            ),
            (
                result["success"],
                result["code"],
                result["data"],
                remaining_requests,
                secret in public_wire,
                tmpdir in public_wire,
                secret in private_log,
                tmpdir in private_log,
            ),
            msg=f"response status failures must be sanitized, privately diagnosable, and cleaned up: {result!r}",
        )

    def test_response_read_failure_redacts_transport_details(self) -> None:
        secret = "ISSUE162_SECRET_RESPONSE_READ"
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir)
            original_exists = Path.exists

            def response_is_ready(path: Path) -> bool:
                if path.parent == watch_dir and path.name.endswith(".response.json"):
                    return True
                return original_exists(path)

            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: tmpdir},
                    clear=False,
                ),
                patch.object(Path, "exists", response_is_ready),
                patch(
                    "prefab_sentinel.bridge_response_io.os.open",
                    side_effect=OSError(secret),
                ),
                self.assertLogs(
                    "prefab_sentinel.editor_bridge",
                    level="ERROR",
                ) as captured,
            ):
                result = send_action(
                    action="capture_screenshot",
                    timeout_sec=1,
                )

        public_wire = json.dumps(result)
        private_log = "\n".join(captured.output)
        self.assertEqual(
            (
                False,
                "EDITOR_BRIDGE_RESPONSE_READ",
                {},
                False,
                False,
                True,
                True,
            ),
            (
                result["success"],
                result["code"],
                result["data"],
                secret in public_wire,
                tmpdir in public_wire,
                secret in private_log,
                tmpdir in private_log,
            ),
            msg=(
                "response read failures must redact public details and retain "
                f"private evidence: {result!r}"
            ),
        )

    def test_response_close_failure_remains_typed_and_redacted(self) -> None:
        secret = "ISSUE163_SECRET_EDITOR_CLOSE"
        closed_fds: list[int] = []
        real_close = os.close

        def close_then_fail(fd: int) -> None:
            closed_fds.append(fd)
            real_close(fd)
            raise OSError(secret)

        class CloseFailingOs:
            def __getattr__(self, name: str) -> Any:
                return getattr(os, name)

            def close(self, fd: int) -> None:
                close_then_fail(fd)

        response_payload: dict[str, object] = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_EDITOR_STATE_OK",
            "message": "ok",
            "data": {},
            "diagnostics": [],
        }
        with (
            patch(
                "prefab_sentinel.bridge_response_io.os",
                CloseFailingOs(),
            ),
            self.assertLogs(
                "prefab_sentinel.editor_bridge",
                level="ERROR",
            ) as captured,
        ):
            result = self._send_with_fake_response(response_payload)

        public_wire = json.dumps(result)
        private_log = "\n".join(captured.output)
        self.assertEqual(
            (
                False,
                "EDITOR_BRIDGE_RESPONSE_READ",
                "Editor bridge response file could not be read.",
                1,
                False,
                True,
            ),
            (
                result["success"],
                result["code"],
                result["message"],
                len(closed_fds),
                secret in public_wire,
                secret in private_log,
            ),
            msg=f"close failure must remain a redacted transport error: {result!r}",
        )

    def test_request_write_failure_includes_watch_dir_blocker(self) -> None:
        secret = "ISSUE162_SECRET_REQUEST_WRITE"
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: tmpdir}, clear=False),
                patch.object(Path, "rename", side_effect=OSError(secret)),
                self.assertLogs("prefab_sentinel.editor_bridge", level="ERROR") as captured,
            ):
                result = send_action(action="capture_screenshot", timeout_sec=1)
            remaining_files = sorted(path.name for path in Path(tmpdir).iterdir())

        public_wire = json.dumps(result)
        private_log = "\n".join(captured.output)
        self.assertEqual(
            (
                False,
                "EDITOR_BRIDGE_WRITE",
                {
                    "blocker_class": "watch_dir",
                    "suggested_next_action": (
                        "Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory."
                    ),
                },
                False,
                False,
                [],
                True,
                True,
            ),
            (
                result["success"],
                result["code"],
                result["data"],
                secret in public_wire,
                tmpdir in public_wire,
                remaining_files,
                secret in private_log,
                tmpdir in private_log,
            ),
            msg=(
                f"request write failures must redact public details, retain private evidence, "
                f"and remove temporary transport files: {result!r}"
            ),
        )

    def test_response_read_successfully(self) -> None:
        """Simulate Unity writing a response file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir)
            seen_request_id: dict[str, str] = {}
            responder_errors: list[BaseException] = []

            import threading

            request_ready = threading.Condition()
            observed_request: dict[str, Path] = {}
            original_rename = Path.rename

            def notifying_rename(self_path: Path, target: str | Path) -> Path:
                renamed_path = original_rename(self_path, target)
                target_path = Path(target)
                if (
                    target_path.parent == watch_dir
                    and target_path.name.endswith(".request.json")
                ):
                    with request_ready:
                        observed_request["path"] = target_path
                        request_ready.notify_all()
                return renamed_path

            def fake_send() -> None:
                """Write a fake response after observing the request file."""
                try:
                    with request_ready:
                        request_seen = request_ready.wait_for(
                            lambda: "path" in observed_request,
                            timeout=2,
                        )
                    if not request_seen:
                        raise AssertionError(
                            "Expected request file before fake Unity response"
                        )

                    request_file = observed_request["path"]
                    base = request_file.name.replace(".request.json", "")
                    seen_request_id["value"] = base
                    resp_path = watch_dir / f"{base}.response.json"
                    resp = {
                        "protocol_version": PROTOCOL_VERSION,
                        "success": True,
                        "severity": "info",
                        "code": "EDITOR_CTRL_SCREENSHOT_OK",
                        "message": "Screenshot captured",
                        "data": {
                            "output_path": "/tmp/test.png",
                            "view": "scene",
                            "width": 800,
                            "height": 600,
                            "executed": True,
                        },
                        "diagnostics": [],
                    }
                    resp_tmp_path = watch_dir / f"{base}.response.json.tmp"
                    resp_tmp_path.write_text(json.dumps(resp), encoding="utf-8")
                    os.replace(resp_tmp_path, resp_path)
                except BaseException as exc:
                    responder_errors.append(exc)

            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: tmpdir},
                    clear=False,
                ),
                patch.object(Path, "rename", notifying_rename),
            ):
                t = threading.Thread(target=fake_send)
                t.start()
                result = send_action(
                    action="capture_screenshot",
                    view="scene",
                    width=800,
                    height=600,
                    timeout_sec=5,
                )
                t.join()

                if responder_errors:
                    raise responder_errors[0]
                self.assertEqual([], responder_errors)
                self.assertTrue(result["success"])
                self.assertEqual("EDITOR_CTRL_SCREENSHOT_OK", result["code"])
                self.assertEqual("/tmp/test.png", result["data"]["output_path"])
                self.assertEqual(seen_request_id["value"], result["request_id"])

    def test_bridge_origin_editor_state_error_includes_blocker_next_action(self) -> None:
        """Simulate Unity returning a live-state failure envelope."""
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir)
            responder_errors: list[BaseException] = []

            import threading

            request_ready = threading.Condition()
            observed_request: dict[str, Path] = {}
            original_rename = Path.rename

            def notifying_rename(self_path: Path, target: str | Path) -> Path:
                renamed_path = original_rename(self_path, target)
                target_path = Path(target)
                if (
                    target_path.parent == watch_dir
                    and target_path.name.endswith(".request.json")
                ):
                    with request_ready:
                        observed_request["path"] = target_path
                        request_ready.notify_all()
                return renamed_path

            def fake_send() -> None:
                try:
                    with request_ready:
                        request_seen = request_ready.wait_for(
                            lambda: "path" in observed_request,
                            timeout=2,
                        )
                    if not request_seen:
                        raise AssertionError(
                            "Expected request file before fake Unity response"
                        )

                    request_file = observed_request["path"]
                    base = request_file.name.replace(".request.json", "")
                    resp_path = watch_dir / f"{base}.response.json"
                    resp = {
                        "protocol_version": PROTOCOL_VERSION,
                        "success": False,
                        "severity": "error",
                        "code": "EDITOR_CTRL_SCENE_BUSY",
                        "message": "Unity is compiling.",
                        "data": {
                            "editor_state": {
                                "is_compiling": True,
                                "is_building_player": False,
                                "state_source": "live_editor",
                            }
                        },
                        "diagnostics": [],
                    }
                    resp_path.write_text(json.dumps(resp), encoding="utf-8")
                except BaseException as exc:
                    responder_errors.append(exc)

            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: tmpdir},
                    clear=False,
                ),
                patch.object(Path, "rename", notifying_rename),
            ):
                t = threading.Thread(target=fake_send)
                t.start()
                result = send_action(action="capture_screenshot", timeout_sec=5)
                t.join()

        if responder_errors:
            raise responder_errors[0]
        self.assertEqual([], responder_errors)
        self.assertEqual(
            (
                False,
                "compile_or_build",
                "Wait for Unity compile or build activity to finish, then retry the tool.",
            ),
            (
                result["success"],
                result["data"].get("blocker_class"),
                result["data"].get("suggested_next_action"),
            ),
        )

    def _send_with_fake_response(
        self,
        response_payload: dict[str, object],
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir)
            responder_errors: list[BaseException] = []

            import threading

            request_ready = threading.Condition()
            observed_request: dict[str, Path] = {}
            original_rename = Path.rename

            def notifying_rename(self_path: Path, target: str | Path) -> Path:
                renamed_path = original_rename(self_path, target)
                target_path = Path(target)
                if (
                    target_path.parent == watch_dir
                    and target_path.name.endswith(".request.json")
                ):
                    with request_ready:
                        observed_request["path"] = target_path
                        request_ready.notify_all()
                return renamed_path

            def fake_send() -> None:
                try:
                    with request_ready:
                        request_seen = request_ready.wait_for(
                            lambda: "path" in observed_request,
                            timeout=2,
                        )
                    if not request_seen:
                        raise AssertionError(
                            "Expected request file before fake Unity response"
                        )

                    request_file = observed_request["path"]
                    request_id = request_file.name.removesuffix(".request.json")
                    response_file = watch_dir / f"{request_id}.response.json"
                    response_tmp_file = watch_dir / f"{request_id}.response.json.tmp"
                    response_tmp_file.write_text(
                        json.dumps(response_payload),
                        encoding="utf-8",
                    )
                    os.replace(response_tmp_file, response_file)
                except BaseException as exc:
                    responder_errors.append(exc)

            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: tmpdir},
                    clear=False,
                ),
                patch.object(Path, "rename", notifying_rename),
            ):
                responder = threading.Thread(target=fake_send)
                responder.start()
                result = send_action(
                    action="get_editor_state",
                    timeout_sec=5,
                )
                responder.join()

        if responder_errors:
            raise responder_errors[0]
        self.assertEqual([], responder_errors)
        return result

    def test_fake_responder_publishes_only_closed_complete_response(self) -> None:
        import threading

        from prefab_sentinel.bridge_response_io import read_bridge_response_file

        write_started = threading.Event()
        consumer_observed = threading.Event()
        original_write_text = Path.write_text
        original_exists = Path.exists

        def paused_response_write(
            self_path: Path,
            data: str,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> int:
            if ".response.json" not in self_path.name:
                return original_write_text(
                    self_path, data, encoding=encoding, errors=errors, newline=newline,
                )
            with self_path.open(
                "w", encoding=encoding, errors=errors, newline=newline,
            ) as stream:
                # Force the consumer to poll while the response writer is open.
                write_started.set()
                self.assertTrue(consumer_observed.wait(timeout=2))
                return stream.write(data)

        def poll_during_write(self_path: Path) -> bool:
            if not self_path.name.endswith(".response.json"):
                return original_exists(self_path)
            self.assertTrue(write_started.wait(timeout=2))
            exists = original_exists(self_path)
            if not exists:
                consumer_observed.set()
            return exists

        def read_before_writer_resumes(response_path: Path) -> object:
            try:
                return read_bridge_response_file(response_path)
            finally:
                consumer_observed.set()

        response_payload: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_STATE_OK",
            "message": "ok",
            "data": {"executed": True},
            "diagnostics": [],
        }
        with (
            patch.object(Path, "write_text", paused_response_write),
            patch.object(Path, "exists", poll_during_write),
            patch(
                "prefab_sentinel.editor_bridge.read_bridge_response_file",
                read_before_writer_resumes,
            ),
        ):
            with self.subTest(responder="common helper"):
                result = self._send_with_fake_response(response_payload)
                self.assertEqual(
                    (True, "EDITOR_CTRL_STATE_OK", {"executed": True}),
                    (result["success"], result["code"], result["data"]),
                )

            write_started.clear()
            consumer_observed.clear()
            with self.subTest(responder="standalone screenshot"):
                self.test_response_read_successfully()

    def test_fake_responder_reraises_serialization_errors(self) -> None:
        response_payload: dict[str, object] = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_STATE_OK",
            "message": "ok",
            "data": {"unserializable": object()},
            "diagnostics": [],
        }

        with self.assertRaisesRegex(
            TypeError,
            "^Object of type object is not JSON serializable$",
        ):
            self._send_with_fake_response(response_payload)

    def test_rejects_malformed_common_envelopes_without_version_update(self) -> None:
        base: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_STATE_OK",
            "message": "ok",
            "data": {},
            "diagnostics": [],
            "bridge_version": "poison-version",
        }
        rows = [
            ("string success", {**base, "success": "false"}),
            ("numeric success", {**base, "success": 1}),
            (
                "missing data",
                {key: value for key, value in base.items() if key != "data"},
            ),
            ("unknown severity", {**base, "severity": "fatal"}),
            ("successful error", {**base, "severity": "error"}),
        ]

        for label, response_payload in rows:
            with (
                self.subTest(label=label),
                patch(
                    "prefab_sentinel.editor_bridge._last_bridge_version",
                    "known-good",
                ),
            ):
                result = self._send_with_fake_response(response_payload)

                self.assertEqual(
                    (False, "error", "EDITOR_BRIDGE_RESPONSE_SCHEMA"),
                    (
                        result["success"],
                        result["severity"],
                        result["code"],
                    ),
                )
                self.assertEqual("known-good", get_last_bridge_version())

    def test_valid_warning_failure_is_preserved_without_version_update(self) -> None:
        response_payload: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "success": False,
            "severity": "warning",
            "code": "EDITOR_DEFERRED",
            "message": "not complete",
            "data": {},
            "diagnostics": [],
            "bridge_version": "failure-version",
        }

        with patch(
            "prefab_sentinel.editor_bridge._last_bridge_version",
            "known-good",
        ):
            result = self._send_with_fake_response(response_payload)

            self.assertEqual(
                response_payload,
                {key: result[key] for key in response_payload},
            )
            self.assertEqual("known-good", get_last_bridge_version())


    def test_protocol_v2_outer_error_is_preserved_and_v1_is_rejected(self) -> None:
        outer_failure: dict[str, object] = {
            "protocol_version": 2,
            "success": False,
            "severity": "error",
            "code": "EDITOR_BRIDGE_ERROR",
            "message": "Editor Bridge request processing failed.",
            "data": {"read_only": True, "executed": False},
            "diagnostics": [],
        }

        current = self._send_with_fake_response(outer_failure)
        legacy = self._send_with_fake_response(
            {**outer_failure, "protocol_version": 1}
        )

        self.assertEqual(
            (
                False,
                2,
                "EDITOR_BRIDGE_ERROR",
                "Editor Bridge request processing failed.",
            ),
            (
                current["success"],
                current["protocol_version"],
                current["code"],
                current["message"],
            ),
        )
        self.assertEqual(
            (False, "EDITOR_BRIDGE_RESPONSE_SCHEMA"),
            (legacy["success"], legacy["code"]),
        )

    def test_success_updates_only_from_non_empty_string_bridge_version(self) -> None:
        base: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_STATE_OK",
            "message": "ok",
            "data": {},
            "diagnostics": [],
        }
        rows = [
            ("non-empty string", "2.0.0", "2.0.0"),
            ("empty string", "", "known-good"),
            ("non-string", 7, "known-good"),
        ]

        for label, bridge_version, expected in rows:
            with (
                self.subTest(label=label),
                patch(
                    "prefab_sentinel.editor_bridge._last_bridge_version",
                    "known-good",
                ),
            ):
                self._send_with_fake_response(
                    {**base, "bridge_version": bridge_version}
                )

                self.assertEqual(expected, get_last_bridge_version())

    def test_watch_directory_identity_is_observed_once_per_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            original_watch_dir = root / "original"
            replacement_watch_dir = root / "replacement"
            original_watch_dir.mkdir()
            replacement_watch_dir.mkdir()
            probed_paths: list[Path] = []
            request_targets: list[Path] = []
            original_is_dir = Path.is_dir
            original_rename = Path.rename

            def mutate_env_after_first_preflight(path: Path) -> bool:
                result = original_is_dir(path)
                probed_paths.append(path)
                if len(probed_paths) == 1:
                    os.environ[BRIDGE_WATCH_DIR_ENV] = str(replacement_watch_dir)
                return result

            def record_request_target(
                source: Path,
                target: str | Path,
            ) -> Path:
                target_path = Path(target)
                if target_path.name.endswith(".request.json"):
                    request_targets.append(target_path.parent)
                return original_rename(source, target)

            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: str(original_watch_dir)},
                    clear=False,
                ),
                patch.object(
                    Path,
                    "is_dir",
                    mutate_env_after_first_preflight,
                ),
                patch.object(Path, "rename", record_request_target),
            ):
                result = send_action(
                    action="capture_screenshot",
                    timeout_sec=1,
                )

        self.assertEqual("EDITOR_BRIDGE_TIMEOUT", result["code"])
        self.assertEqual(
            [original_watch_dir, original_watch_dir],
            probed_paths,
        )
        self.assertEqual([original_watch_dir], request_targets)


class EditorBridgeBlockerDiagnosticTests(unittest.TestCase):
    def test_watch_dir_errors_include_blocker_data(self) -> None:
        expected_action = "Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory."
        with tempfile.NamedTemporaryFile() as tmp_file:
            cases = (
                (
                    {BRIDGE_WATCH_DIR_ENV: ""},
                    check_editor_bridge_env,
                    "EDITOR_BRIDGE_WATCH_DIR_MISSING",
                ),
                (
                    {BRIDGE_WATCH_DIR_ENV: tmp_file.name},
                    check_editor_bridge_env,
                    "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
                ),
                (
                    {BRIDGE_WATCH_DIR_ENV: ""},
                    lambda: send_action(action="capture_screenshot"),
                    "EDITOR_BRIDGE_WATCH_DIR_MISSING",
                ),
            )
            for env, call, expected_code in cases:
                with self.subTest(expected_code=expected_code, env=env):
                    with patch.dict(os.environ, env, clear=False):
                        result = require_not_none(call(), f"{expected_code} returned no envelope")
                    self.assertEqual(expected_code, result["code"])
                    self.assertEqual(
                        ("watch_dir", expected_action),
                        (
                            result["data"].get("blocker_class"),
                            result["data"].get("suggested_next_action"),
                        ),
                    )

    def test_response_timeout_includes_bridge_connection_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: tmpdir}, clear=False):
                result = send_action(action="capture_screenshot", timeout_sec=1)

        self.assertEqual("EDITOR_BRIDGE_TIMEOUT", result["code"])
        self.assertEqual(
            (
                "bridge_connection",
                "Confirm Unity is running and the PrefabSentinel Editor Bridge watcher is active.",
            ),
            (
                result["data"].get("blocker_class"),
                result["data"].get("suggested_next_action"),
            ),
        )

    def test_invalid_timeout_keeps_caller_input_error_without_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: tmpdir}, clear=False):
                result = send_action(action="capture_screenshot", timeout_sec=0)

        self.assertEqual("EDITOR_BRIDGE_TIMEOUT_INVALID", result["code"])
        self.assertNotIn("blocker_class", result["data"])
        self.assertNotIn("suggested_next_action", result["data"])


class BridgeProjectRootMismatchTests(unittest.TestCase):
    _OMIT_EXPECTED_ROOT = object()

    def _send_with_fake_response(
        self,
        response_payload: dict[str, object],
        *,
        expected_project_root: str | None | object = _OMIT_EXPECTED_ROOT,
        request_extras: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir)
            seen_request_id: dict[str, str] = {}
            seen_request_payload: dict[str, Any] = {}
            responder_errors: list[BaseException] = []

            import threading

            request_ready = threading.Condition()
            observed_request: dict[str, Path] = {}
            original_rename = Path.rename

            def notifying_rename(self_path: Path, target: str | Path) -> Path:
                renamed_path = original_rename(self_path, target)
                target_path = Path(target)
                if (
                    target_path.parent == watch_dir
                    and target_path.name.endswith(".request.json")
                ):
                    with request_ready:
                        observed_request["path"] = target_path
                        request_ready.notify_all()
                return renamed_path

            def fake_send() -> None:
                try:
                    with request_ready:
                        request_seen = request_ready.wait_for(
                            lambda: "path" in observed_request,
                            timeout=2,
                        )
                    if not request_seen:
                        raise AssertionError(
                            "Expected request file before fake Unity response"
                        )

                    request_file = observed_request["path"]
                    request_id = request_file.name.removesuffix(".request.json")
                    seen_request_id["value"] = request_id
                    seen_request_payload.update(
                        json.loads(request_file.read_text(encoding="utf-8"))
                    )
                    response = dict(response_payload)
                    response.setdefault("protocol_version", PROTOCOL_VERSION)
                    response_file = watch_dir / f"{request_id}.response.json"
                    tmp_response_file = Path(str(response_file) + ".tmp")
                    tmp_response_file.write_text(
                        json.dumps(response),
                        encoding="utf-8",
                    )
                    tmp_response_file.rename(response_file)
                except BaseException as exc:
                    responder_errors.append(exc)

            with (
                patch.dict(
                    os.environ,
                    {BRIDGE_WATCH_DIR_ENV: tmpdir},
                    clear=False,
                ),
                patch.object(Path, "rename", notifying_rename),
            ):
                t = threading.Thread(target=fake_send)
                t.start()
                if expected_project_root is self._OMIT_EXPECTED_ROOT:
                    result = send_action(
                        action="get_editor_state",
                        timeout_sec=5,
                        request_extras=request_extras,
                    )
                else:
                    if not isinstance(expected_project_root, str):
                        raise AssertionError(
                            "expected_project_root must be a string when provided"
                        )
                    result = send_action(
                        action="get_editor_state",
                        timeout_sec=5,
                        request_extras=request_extras,
                        expected_project_root=expected_project_root,
                    )
                t.join()

        if responder_errors:
            raise responder_errors[0]
        self.assertEqual([], responder_errors)
        return result, seen_request_id["value"], seen_request_payload

    def test_mismatching_project_root_returns_typed_error_with_identity(self) -> None:
        expected_root = "/workspace/ExpectedProject"
        actual_root = "/workspace/OtherProject"
        with patch(
            "prefab_sentinel.editor_bridge._last_bridge_version",
            "known-good",
        ):
            result, request_id, request_payload = self._send_with_fake_response(
                {
                    "success": True,
                    "severity": "info",
                    "code": "EDITOR_CTRL_STATE_OK",
                    "message": "Editor state captured",
                    "data": {"is_playing": False},
                    "diagnostics": [],
                    "bridge_version": "poison-version",
                    "operator_context": {
                        "project_root": actual_root,
                        "bridge_session_id": "bridge-session-1",
                        "bridge_instance_id": "bridge-instance-1",
                        "bridge_version": "poison-version",
                    },
                },
                expected_project_root=expected_root,
            )

            self.assertEqual("known-good", get_last_bridge_version())

        self.assertEqual(False, result["success"], result)
        self.assertEqual("EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH", result["code"])
        self.assertEqual("error", result["severity"])
        self.assertIn(expected_root, result["message"])
        self.assertIn(actual_root, result["message"])
        data = require_mapping(result["data"], "mismatch data")
        self.assertEqual("get_editor_state", data["action"])
        self.assertEqual(request_id, data["request_id"])
        self.assertEqual(expected_root, data["expected_project_root"])
        self.assertEqual(expected_root, request_payload["expected_project_root"])
        self.assertEqual(actual_root, data["actual_project_root"])
        self.assertEqual("bridge-session-1", data["bridge_session_id"])
        self.assertEqual("bridge-instance-1", data["bridge_instance_id"])

    def test_expected_root_requires_actual_root_identity(self) -> None:
        expected_root = "/workspace/ExpectedProject"
        result, request_id, request_payload = self._send_with_fake_response(
            {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_STATE_OK",
                "message": "Editor state captured",
                "data": {},
                "diagnostics": [],
                "operator_context": {
                    "bridge_session_id": "bridge-session-1",
                    "bridge_instance_id": "bridge-instance-1",
                },
            },
            expected_project_root=expected_root,
        )

        self.assertEqual(False, result["success"], result)
        self.assertEqual("EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH", result["code"])
        self.assertIn("actual Unity project root", result["message"])
        self.assertIn(expected_root, result["message"])
        data = require_mapping(result["data"], "mismatch data")
        self.assertEqual(request_id, data["request_id"])
        self.assertEqual(expected_root, data["expected_project_root"])
        self.assertEqual(expected_root, request_payload["expected_project_root"])
        self.assertNotIn("actual_project_root", data)

    def test_matching_project_root_preserves_success_payload(self) -> None:
        expected_root = "/workspace/ExpectedProject"
        result, request_id, request_payload = self._send_with_fake_response(
            {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_STATE_OK",
                "message": "Editor state captured",
                "data": {"is_playing": False},
                "diagnostics": [],
                "operator_context": {
                    "project_root": expected_root,
                    "bridge_session_id": "bridge-session-1",
                    "bridge_instance_id": "bridge-instance-1",
                },
            },
            expected_project_root=expected_root,
        )

        self.assertEqual(True, result["success"], result)
        self.assertEqual("EDITOR_CTRL_STATE_OK", result["code"])
        self.assertEqual(request_id, result["request_id"])
        operator_context = require_mapping(result["operator_context"], "operator context")
        data = require_mapping(result["data"], "success data")
        self.assertEqual(expected_root, operator_context["project_root"])
        self.assertEqual(expected_root, request_payload["expected_project_root"])
        self.assertEqual(False, data["is_playing"])

    def test_request_extras_cannot_override_expected_project_root(self) -> None:
        expected_root = "/workspace/ExpectedProject"
        _, _, request_payload = self._send_with_fake_response(
            {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_STATE_OK",
                "message": "Editor state captured",
                "data": {"is_playing": False},
                "diagnostics": [],
                "operator_context": {
                    "project_root": expected_root,
                    "bridge_session_id": "bridge-session-1",
                    "bridge_instance_id": "bridge-instance-1",
                },
            },
            expected_project_root=expected_root,
            request_extras={"expected_project_root": "/workspace/AttackerProject"},
        )

        self.assertEqual(expected_root, request_payload["expected_project_root"])


class TestEditorBridgeSupportedActions(unittest.TestCase):
    """Issue #193 — membership test: the supported-actions set contains
    exactly one prefab-save action (``safe_save_prefab``), and contains
    no other prefab-save action name (e.g. the legacy ``save_as_prefab``).
    """

    def test_safe_save_prefab_is_only_prefab_save_action(self) -> None:
        self.assertIn("safe_save_prefab", SUPPORTED_ACTIONS)
        # No other prefab-save action.
        self.assertNotIn("save_as_prefab", SUPPORTED_ACTIONS)


class TestSupportedActions(unittest.TestCase):
    """Verify the action set is complete."""

    def test_all_actions_present(self) -> None:
        expected = {
            "capture_screenshot",
            "select_object",
            "frame_selected",
            "instantiate_to_scene",
            "ping_object",
            "capture_console_logs",
            "refresh_asset_database",
            "set_material",
            "delete_object",
            "list_children",
            "list_materials",
            "get_camera",
            "set_camera",
            "list_roots",
            "get_material_property",
            "set_material_property",
            "run_integration_tests",
            "vrcsdk_upload",
            # Phase 2: BlendShape + Menu
            "get_blend_shapes",
            "set_blend_shape",
            "list_menu_items",
            "execute_menu_item",
            "find_renderers_by_material",
            "editor_rename",
            "editor_add_component",
            "editor_remove_component",
            "create_udon_program_asset",
            "editor_set_property",
            "editor_serialized_property_read",
            "editor_serialized_property_list",
            "editor_inspect_serialized_surface",
            "editor_serialized_property_write",
            "create_generated_asset",
            "move_asset",
            # Issue #193: ``safe_save_prefab`` is the sole public prefab-save action.
            "safe_save_prefab",
            "editor_set_parent",
            "editor_create_empty",
            "editor_create_primitive",
            # Issue #195: dedicated uGUI element creation surface.
            "editor_create_ui_element",
            "editor_batch_create",
            "editor_batch_set_property",
            "editor_batch_set_material_property",
            "editor_open_scene",
            "editor_save_scene",
            "editor_batch_add_component",
            "editor_create_scene",
            "editor_reflect",
            # Phase: one-step C# exec (#74)
            "run_script",
            # Issue #118: synchronous recompile-and-wait surface
            "editor_recompile_and_wait",
            # Issue #119: high-level UdonSharp authoring surface.
            "editor_add_udonsharp_component",
            "editor_set_udonsharp_field",
            "editor_wire_persistent_listener",
            # Issue #239: read-only editor-state snapshot.
            "get_editor_state",
            # Issue #242: bridge-side scene-view refresh primitive.
            "force_scene_view_refresh",
            # Issue #240: batch blend-shape write under one Undo group.
            "batch_set_blend_shape",
            # Issue #236: Prefab Stage open / close.
            "open_prefab",
            "close_prefab",
            # Issue #233: async run-script submit / poll.
            "run_script_submit",
            "run_script_poll",
            # Issue #243: AnimationClip primitives.
            "inspect_animation_clip",
            "create_animation_clip",
            "apply_animation_clip",
            # Issue #98: live geometry read primitives.
            "get_transform",
            "get_bounds",
            "measure_distance",
            # Issue #114: AssetDatabase-backed asset deletion.
            "delete_assets",
        }
        self.assertEqual(expected, SUPPORTED_ACTIONS)


class TestCameraActions(unittest.TestCase):
    """Tests for get_camera / set_camera action validation."""

    def test_get_camera_in_supported_actions(self) -> None:
        self.assertIn("get_camera", SUPPORTED_ACTIONS)

    def test_set_camera_in_supported_actions(self) -> None:
        self.assertIn("set_camera", SUPPORTED_ACTIONS)

    def test_old_camera_removed(self) -> None:
        self.assertNotIn("camera", SUPPORTED_ACTIONS)

    def test_get_camera_env_missing(self) -> None:
        """get_camera returns bridge error when env not configured."""
        with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: ""}, clear=False):
            result = send_action(action="get_camera")
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_MISSING", result["code"])

    def test_set_camera_env_missing(self) -> None:
        """set_camera returns bridge error when env not configured."""
        with patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: ""}, clear=False):
            result = send_action(action="set_camera", yaw=0.0)
            self.assertFalse(result["success"])
            self.assertEqual("EDITOR_BRIDGE_WATCH_DIR_MISSING", result["code"])


class TestSetCameraParams(unittest.TestCase):
    """Validate editor_set_camera parameter conversion."""

    def test_pivot_orbit_kwargs(self) -> None:
        # Issue #81: orbit-radius argument is named ``size`` (it
        # carries ``SceneView.size`` semantics — Scene-view half-width).
        kwargs = build_set_camera_kwargs(
            pivot='{"x":0,"y":1.3,"z":0}',
            yaw=345.0,
            pitch=8.0,
            size=0.28,
        )
        self.assertEqual(kwargs["camera_pivot"], [0, 1.3, 0])
        self.assertEqual(kwargs["yaw"], 345.0)
        self.assertEqual(kwargs["pitch"], 8.0)
        self.assertEqual(kwargs["size"], 0.28)
        self.assertNotIn("camera_position", kwargs)
        self.assertNotIn("camera_look_at", kwargs)
        # The pre-rename key must not survive as a hidden alias.
        self.assertNotIn("distance", kwargs)

    def test_position_look_at_kwargs(self) -> None:
        kwargs = build_set_camera_kwargs(
            position='{"x":0,"y":1.5,"z":-1}',
            look_at='{"x":0,"y":1.3,"z":0}',
        )
        self.assertEqual(kwargs["camera_position"], [0, 1.5, -1])
        self.assertEqual(kwargs["camera_look_at"], [0, 1.3, 0])
        self.assertNotIn("camera_pivot", kwargs)

    def test_position_yaw_pitch_kwargs(self) -> None:
        kwargs = build_set_camera_kwargs(
            position='{"x":0,"y":1.5,"z":-1}',
            yaw=0.0,
            pitch=10.0,
            size=0.5,
        )
        self.assertEqual(kwargs["camera_position"], [0, 1.5, -1])
        self.assertEqual(kwargs["yaw"], 0.0)
        self.assertEqual(kwargs["pitch"], 10.0)
        self.assertEqual(kwargs["size"], 0.5)
        self.assertNotIn("camera_look_at", kwargs)
        self.assertNotIn("distance", kwargs)

    def test_omitted_params_excluded(self) -> None:
        kwargs = build_set_camera_kwargs(yaw=180.0)
        self.assertEqual(kwargs, {"yaw": 180.0})

    def test_size_sentinel_minus_one_is_omitted(self) -> None:
        # Issue #81: the orbit-radius sentinel ``-1.0`` means "keep
        # current" and must not be forwarded verbatim; the produced
        # kwargs dict must contain neither ``size`` nor the pre-rename
        # alias ``distance``.
        kwargs = build_set_camera_kwargs(size=-1.0)
        self.assertEqual(kwargs, {})

    def test_distance_is_not_a_registered_builder_parameter(self) -> None:
        # Issue #81: the pre-rename keyword name must not survive as a
        # hidden alias on the builder's keyword surface; callers that
        # still pass ``distance=...`` should get the standard Python
        # ``TypeError`` for an unknown keyword argument.
        import inspect

        params = inspect.signature(build_set_camera_kwargs).parameters
        self.assertIn("size", params)
        self.assertNotIn("distance", params)

    def test_orthographic_passed(self) -> None:
        kwargs = build_set_camera_kwargs(orthographic=1)
        self.assertEqual(kwargs["camera_orthographic"], 1)

    def test_reset_to_defaults_passed(self) -> None:
        """Issue #112: ``reset_to_defaults=True`` is forwarded to the bridge."""
        kwargs = build_set_camera_kwargs(reset_to_defaults=True)
        self.assertEqual({"reset_to_defaults": True}, kwargs)

    def test_reset_to_defaults_default_false_omitted(self) -> None:
        kwargs = build_set_camera_kwargs(yaw=0.0)
        self.assertNotIn("reset_to_defaults", kwargs)


class TestEditorSetCameraForwardsResetToDefaults(unittest.TestCase):
    """Issue #112: the MCP wrapper forwards the flag through ``send_action``."""

    def test_editor_set_camera_forwards_reset_to_defaults(self) -> None:
        from prefab_sentinel import mcp_tools_editor_view  # noqa: PLC0415
        from prefab_sentinel.mcp_server import create_server  # noqa: PLC0415
        from tests._mcp_test_support import (  # noqa: PLC0415
            call_tool_result,
            structured_payload,
        )

        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_SET_CAMERA_OK",
                "message": "ok",
                "data": {},
                "diagnostics": [],
            }
            server = create_server()
            result = call_tool_result(
                server,
                "editor_set_camera",
                {"reset_to_defaults": True},
            )
            self.assertIs(result.is_error, False)
            self.assertEqual(
                {
                    "success": True,
                    "severity": "info",
                    "code": "EDITOR_CTRL_SET_CAMERA_OK",
                    "message": "ok",
                    "data": {},
                    "diagnostics": [],
                },
                structured_payload(result),
            )
        kwargs = send.call_args.kwargs
        self.assertEqual("set_camera", kwargs["action"])
        self.assertTrue(kwargs.get("reset_to_defaults"))


class TestEditorConsoleForwardsClassificationFilter(unittest.TestCase):
    """Issue #117: the MCP wrapper forwards ``classification_filter``."""

    def test_editor_console_classification_filter_forwarded(self) -> None:
        from prefab_sentinel import mcp_tools_editor_view  # noqa: PLC0415
        from prefab_sentinel.mcp_server import create_server  # noqa: PLC0415
        from tests._mcp_test_support import (  # noqa: PLC0415
            call_tool_result,
            structured_payload,
        )

        with patch.object(mcp_tools_editor_view, "send_action") as send:
            send.return_value = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_CONSOLE_OK",
                "message": "ok",
                "data": {"entries": []},
                "diagnostics": [],
            }
            server = create_server()
            result = call_tool_result(
                server,
                "editor_console",
                {"classification_filter": "non_fatal"},
            )
            self.assertIs(result.is_error, False)
            self.assertEqual(
                {
                    "success": True,
                    "severity": "info",
                    "code": "EDITOR_CTRL_CONSOLE_OK",
                    "message": "ok",
                    "data": {"entries": []},
                    "diagnostics": [],
                },
                structured_payload(result),
            )
        kwargs = send.call_args.kwargs
        self.assertEqual("capture_console_logs", kwargs["action"])
        self.assertEqual("non_fatal", kwargs["classification_filter"])


class TestCreateEmptyKwargs(unittest.TestCase):
    """I4: build_create_empty_kwargs omits empty optional fields."""

    def test_name_only(self) -> None:
        result = build_create_empty_kwargs(name="Obj")
        self.assertEqual(result, {"new_name": "Obj"})
        self.assertNotIn("hierarchy_path", result)
        self.assertNotIn("property_value", result)

    def test_with_parent(self) -> None:
        result = build_create_empty_kwargs(name="Obj", parent_path="/Root")
        self.assertEqual(result, {"new_name": "Obj", "hierarchy_path": "/Root"})

    def test_with_position(self) -> None:
        result = build_create_empty_kwargs(name="Obj", position="1,2,3")
        self.assertEqual(result, {"new_name": "Obj", "property_value": "1,2,3"})

    def test_all_specified(self) -> None:
        result = build_create_empty_kwargs(name="Obj", parent_path="/Root", position="1,2,3")
        self.assertEqual(result, {"new_name": "Obj", "hierarchy_path": "/Root", "property_value": "1,2,3"})

    def test_empty_strings_omitted(self) -> None:
        result = build_create_empty_kwargs(name="Obj", parent_path="", position="")
        self.assertEqual(result, {"new_name": "Obj"})
        self.assertNotIn("hierarchy_path", result)
        self.assertNotIn("property_value", result)


class TestResolveAssetPath(unittest.TestCase):
    """Validate resolve_asset_path joins Assets/... paths with project root."""

    def test_relative_assets_path_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            assets_dir = Path(tmpdir) / "Assets"
            assets_dir.mkdir()
            fake_asset = assets_dir / "test.prefab"
            fake_asset.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Test\n",
                encoding="utf-8",
            )

            resolved = resolve_asset_path("Assets/test.prefab", Path(tmpdir))
            self.assertEqual(resolved, fake_asset.resolve())

    def test_absolute_path_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_asset = Path(tmpdir) / "test.prefab"
            fake_asset.write_text("%YAML 1.1\n", encoding="utf-8")

            resolved = resolve_asset_path(str(fake_asset), Path(tmpdir))
            self.assertEqual(resolved, fake_asset)

    def test_no_project_root_returns_as_is(self) -> None:
        resolved = resolve_asset_path("Assets/nonexistent.prefab", None)
        self.assertEqual(resolved, Path("Assets/nonexistent.prefab"))

    def test_path_traversal_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as ctx:
                resolve_asset_path("Assets/../../etc/passwd", Path(tmpdir))
            self.assertIn("escapes project root", str(ctx.exception))

    def test_absolute_outside_root_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as ctx:
                resolve_asset_path("/outside/path.prefab", Path(tmpdir))
            self.assertIn("escapes project root", str(ctx.exception))

    def test_valid_relative_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            assets_dir = Path(tmpdir) / "Assets"
            assets_dir.mkdir()
            asset = assets_dir / "test.prefab"
            asset.write_text("%YAML 1.1\n", encoding="utf-8")
            resolved = resolve_asset_path("Assets/test.prefab", Path(tmpdir))
            self.assertEqual(resolved, asset.resolve())

    def test_no_root_skips_guard(self) -> None:
        resolved = resolve_asset_path("../../etc/passwd", None)
        self.assertEqual(resolved, Path("../../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
