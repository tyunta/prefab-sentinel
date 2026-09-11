from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, cast

from tests.bridge_test_helpers import EditorBridgeResponder


class _WorkerFailure(BaseException):
    pass


class _StuckThread:
    def __init__(self) -> None:
        self.join_timeout: float | None = None

    def start(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        self.join_timeout = timeout

    def is_alive(self) -> bool:
        return True


class EditorBridgeResponderTests(unittest.TestCase):
    def test_worker_exception_is_reraised_on_context_exit(self) -> None:
        worker_called = threading.Event()

        def fail(_request: dict[str, Any]) -> dict[str, Any]:
            worker_called.set()
            raise _WorkerFailure("worker boom")

        with tempfile.TemporaryDirectory() as raw_watch_dir:
            watch_dir = Path(raw_watch_dir)
            with self.assertRaisesRegex(_WorkerFailure, r"^worker boom$"):
                with EditorBridgeResponder(watch_dir, fail):
                    (watch_dir / "case.request.json").write_text(
                        json.dumps({"action": "probe"}),
                        encoding="utf-8",
                    )
                    self.assertTrue(worker_called.wait(timeout=1.0))

    def test_normal_response_is_published_and_worker_stops(self) -> None:
        worker_called = threading.Event()

        def respond(_request: dict[str, Any]) -> dict[str, Any]:
            worker_called.set()
            return {"success": True}

        with tempfile.TemporaryDirectory() as raw_watch_dir:
            watch_dir = Path(raw_watch_dir)
            with EditorBridgeResponder(watch_dir, respond) as responder:
                (watch_dir / "case.request.json").write_text(
                    json.dumps({"action": "probe"}),
                    encoding="utf-8",
                )
                self.assertTrue(worker_called.wait(timeout=1.0))

            self.assertFalse(responder._thread.is_alive())
            self.assertEqual(
                {"protocol_version": 2, "success": True},
                json.loads(
                    (watch_dir / "case.response.json").read_text(encoding="utf-8")
                ),
            )
            self.assertFalse((watch_dir / "case.response.json.tmp").exists())

    def test_worker_stop_timeout_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as raw_watch_dir:
            responder = EditorBridgeResponder(Path(raw_watch_dir), lambda request: {})
            stuck_thread = _StuckThread()
            responder._thread = cast(Any, stuck_thread)

            with self.assertRaisesRegex(
                RuntimeError,
                r"^EditorBridgeResponder worker did not stop within 2\.0 seconds\.$",
            ):
                with responder:
                    pass

            self.assertEqual(2.0, stuck_thread.join_timeout)

    def test_context_body_exception_is_not_masked_by_worker_failure(self) -> None:
        worker_called = threading.Event()

        def fail(_request: dict[str, Any]) -> dict[str, Any]:
            worker_called.set()
            raise _WorkerFailure("worker boom")

        with tempfile.TemporaryDirectory() as raw_watch_dir:
            watch_dir = Path(raw_watch_dir)
            with self.assertRaisesRegex(ValueError, r"^body boom$"):
                with EditorBridgeResponder(watch_dir, fail):
                    (watch_dir / "case.request.json").write_text(
                        json.dumps({"action": "probe"}),
                        encoding="utf-8",
                    )
                    self.assertTrue(worker_called.wait(timeout=1.0))
                    raise ValueError("body boom")
