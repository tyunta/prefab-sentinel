"""Tests for bounded Editor Bridge response-file reads."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prefab_sentinel.bridge_response_io import (
    BridgeResponseReadError,
    read_bridge_response_file,
)


class BridgeResponseFileReaderTests(unittest.TestCase):
    def test_cap_boundary_is_inclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "prefab_sentinel.bridge_response_io.BRIDGE_RESPONSE_MAX_BYTES",
                8,
            ):
                for size, accepted in ((7, True), (8, True), (9, False)):
                    with self.subTest(size=size):
                        path = root / f"{size}.response.json"
                        path.write_bytes(b"null".ljust(size, b" "))
                        if accepted:
                            self.assertEqual(None, read_bridge_response_file(path))
                        else:
                            with self.assertRaisesRegex(
                                BridgeResponseReadError,
                                "size limit",
                            ):
                                read_bridge_response_file(path)

    def test_reads_regular_utf8_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response.json"
            path.write_bytes(b'{"ok": true}')

            self.assertEqual({"ok": True}, read_bridge_response_file(path))

    def test_rejects_invalid_utf8_without_exposing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response.json"
            path.write_bytes(b"\xff")

            with self.assertRaisesRegex(
                BridgeResponseReadError,
                "^Bridge response could not be read as JSON\\.$",
            ) as captured:
                read_bridge_response_file(path)

        self.assertNotIn("0xff", str(captured.exception))

    def test_rejects_invalid_json_without_exposing_content(self) -> None:
        secret = "ISSUE163_SECRET_INVALID_JSON"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response.json"
            path.write_text(secret, encoding="utf-8")

            with self.assertRaisesRegex(
                BridgeResponseReadError,
                "^Bridge response could not be read as JSON\\.$",
            ) as captured:
                read_bridge_response_file(path)

        self.assertNotIn(secret, str(captured.exception))

    def test_rejects_non_standard_number_constants(self) -> None:
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "response.json"
                    path.write_text(literal, encoding="utf-8")

                    with self.assertRaises(BridgeResponseReadError) as captured:
                        read_bridge_response_file(path)

                self.assertEqual(
                    "Bridge response could not be read as JSON.",
                    str(captured.exception),
                )
                self.assertNotIn(literal, str(captured.exception))

    def test_rejects_exponent_overflow_without_exposing_literal(self) -> None:
        for literal in ("1e400", "-1e400"):
            with self.subTest(literal=literal):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "response.json"
                    path.write_text(literal, encoding="utf-8")

                    with self.assertRaises(BridgeResponseReadError) as captured:
                        read_bridge_response_file(path)

                self.assertEqual(
                    "Bridge response could not be read as JSON.",
                    str(captured.exception),
                )
                self.assertNotIn(literal, str(captured.exception))

    def test_accepts_finite_exponent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response.json"
            path.write_text('{"value": 1e308}', encoding="utf-8")

            self.assertEqual({"value": 1e308}, read_bridge_response_file(path))

    def test_close_failures_are_redacted_without_double_close(self) -> None:
        def assert_close_failure(content: bytes, label: str) -> None:
            secret = f"ISSUE163_SECRET_CLOSE_{label.upper()}"
            closed_fds: list[int] = []
            real_close = os.close

            def close_then_fail(fd: int) -> None:
                closed_fds.append(fd)
                real_close(fd)
                raise OSError(secret)

            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "response.json"
                path.write_bytes(content)

                with (
                    mock.patch(
                        "prefab_sentinel.bridge_response_io.os.close",
                        side_effect=close_then_fail,
                    ),
                    self.assertRaises(BridgeResponseReadError) as captured,
                ):
                    read_bridge_response_file(path)

            self.assertEqual(
                "Bridge response could not be read as JSON.",
                str(captured.exception),
            )
            self.assertNotIn(secret, str(captured.exception))
            self.assertEqual(
                1,
                len(closed_fds),
                msg=f"descriptor must be closed exactly once: {closed_fds!r}",
            )
            with self.assertRaises(OSError):
                os.fstat(closed_fds[0])

        for label, content in (("valid", b"null"), ("invalid", b"not-json")):
            with self.subTest(label=label):
                assert_close_failure(content, label)

    def test_rejects_missing_file_without_exposing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ISSUE163_SECRET_MISSING.response.json"

            with self.assertRaisesRegex(
                BridgeResponseReadError,
                "^Bridge response could not be read as JSON\\.$",
            ) as captured:
                read_bridge_response_file(path)

        self.assertNotIn(str(path), str(captured.exception))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.response.json"
            target.write_text("null", encoding="utf-8")
            link = root / "link.response.json"
            link.symlink_to(target)

            with self.assertRaises(BridgeResponseReadError) as captured:
                read_bridge_response_file(link)

        self.assertNotIn(str(link), str(captured.exception))

    def test_rejects_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response.json"
            path.mkdir()

            with self.assertRaisesRegex(
                BridgeResponseReadError,
                "regular file",
            ):
                read_bridge_response_file(path)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs are unavailable")
    def test_rejects_fifo_without_starting_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response.json"
            os.mkfifo(path)

            with self.assertRaisesRegex(
                BridgeResponseReadError,
                "regular file",
            ):
                read_bridge_response_file(path)


if __name__ == "__main__":
    unittest.main()
