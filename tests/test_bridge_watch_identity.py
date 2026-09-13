from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import prefab_sentinel.bridge_watch_identity as bridge_watch_identity
from prefab_sentinel.bridge_watch_identity import (
    BRIDGE_STATUS_MAX_BYTES,
    BRIDGE_STATUS_RELATIVE_PATH,
    WATCH_IDENTITY_MARKER_FILENAME,
    WatchIdentityObservation,
    ensure_watch_identity,
    observe_watch_identity,
)


class WatchIdentityMarkerTests(unittest.TestCase):
    def test_absent_marker_is_created_once_and_reused(self) -> None:
        from uuid import UUID

        fixed = UUID("01234567-89ab-cdef-0123-456789abcdef")
        with tempfile.TemporaryDirectory() as raw:
            watch_dir = Path(raw)
            with patch("prefab_sentinel.bridge_watch_identity.uuid.uuid4", return_value=fixed):
                first = ensure_watch_identity(watch_dir)
                second = ensure_watch_identity(watch_dir)
        self.assertEqual("0123456789abcdef0123456789abcdef", first)
        self.assertEqual(first, second)

    def test_symlink_alias_reads_the_same_physical_marker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            target_identity = ensure_watch_identity(target)
            alias_identity = ensure_watch_identity(alias)
        self.assertEqual(target_identity, alias_identity)

    def test_invalid_existing_marker_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            watch_dir = Path(raw)
            marker = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
            marker.write_text("INVALID_SECRET", encoding="utf-8")
            observed = ensure_watch_identity(watch_dir)
            persisted = marker.read_text(encoding="utf-8")
        self.assertIsNone(observed)
        self.assertEqual("INVALID_SECRET", persisted)

    def test_marker_must_be_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            watch_dir = Path(raw)
            marker = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
            marker.mkdir()
            self.assertIsNone(ensure_watch_identity(watch_dir))

    def test_marker_symlink_and_fifo_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            watch_dir = Path(raw)
            target = watch_dir / "target"
            target.write_text("a" * 32, encoding="ascii")
            marker = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
            marker.symlink_to(target)
            self.assertIsNone(ensure_watch_identity(watch_dir))
            marker.unlink()
            os.mkfifo(marker)
            self.assertIsNone(ensure_watch_identity(watch_dir))

    def test_marker_read_uses_a_33_byte_ceiling(self) -> None:
        identity = "a" * 32
        with tempfile.TemporaryDirectory() as raw:
            watch_dir = Path(raw)
            marker = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
            marker.write_text(identity, encoding="ascii")

            def oversized_read(fd: int, byte_count: int) -> bytes:
                self.assertGreaterEqual(fd, 0)
                self.assertEqual(33, byte_count)
                return b"a" * 33

            with patch(
                "prefab_sentinel.bridge_watch_identity.os.read",
                side_effect=oversized_read,
            ):
                observed = ensure_watch_identity(watch_dir)
        self.assertIsNone(observed)

    def test_marker_read_does_not_use_unbounded_path_helper(self) -> None:
        identity = "a" * 32
        with tempfile.TemporaryDirectory() as raw:
            watch_dir = Path(raw)
            marker = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
            marker.write_text(identity, encoding="ascii")
            with patch.object(Path, "read_bytes", side_effect=MemoryError):
                observed = ensure_watch_identity(watch_dir)
        self.assertEqual(identity, observed)

    def test_marker_descriptor_size_and_kind_are_revalidated(self) -> None:
        identity = "a" * 32
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            watch_dir = root / "watch"
            watch_dir.mkdir()
            marker = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
            marker.write_text(identity, encoding="ascii")
            short = root / "short"
            short.write_bytes(b"a" * 31)
            oversized = root / "oversized"
            oversized.write_bytes(b"a" * 33)

            for descriptor_info in (short.stat(), oversized.stat(), root.stat()):
                with self.subTest(
                    descriptor_size=descriptor_info.st_size,
                    descriptor_mode=descriptor_info.st_mode,
                ):
                    with patch(
                        "prefab_sentinel.bridge_watch_identity.os.fstat",
                        return_value=descriptor_info,
                    ):
                        observed = ensure_watch_identity(watch_dir)
                    self.assertIsNone(observed)

    def test_short_and_growing_marker_reads_are_unavailable(self) -> None:
        identity = "a" * 32
        with tempfile.TemporaryDirectory() as raw:
            watch_dir = Path(raw)
            marker = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
            marker.write_text(identity, encoding="ascii")
            for payload in (b"a" * 31, b"a" * 33):
                with self.subTest(read_size=len(payload)):
                    with patch(
                        "prefab_sentinel.bridge_watch_identity.os.read",
                        return_value=payload,
                    ):
                        observed = ensure_watch_identity(watch_dir)
                    self.assertIsNone(observed)

    def test_marker_read_error_is_unavailable(self) -> None:
        identity = "a" * 32
        with tempfile.TemporaryDirectory() as raw:
            watch_dir = Path(raw)
            marker = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
            marker.write_text(identity, encoding="ascii")
            with patch(
                "prefab_sentinel.bridge_watch_identity.os.read",
                side_effect=OSError("SECRET_READ_ERROR"),
            ):
                observed = ensure_watch_identity(watch_dir)
        self.assertIsNone(observed)

    def test_marker_descriptor_is_closed_when_reading_fails(self) -> None:
        identity = "a" * 32
        with tempfile.TemporaryDirectory() as raw:
            watch_dir = Path(raw)
            marker = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
            marker.write_text(identity, encoding="ascii")
            descriptor = os.open(marker, os.O_RDONLY)
            descriptor_closed = False
            try:
                with (
                    patch(
                        "prefab_sentinel.bridge_watch_identity.os.open",
                        return_value=descriptor,
                    ),
                    patch(
                        "prefab_sentinel.bridge_watch_identity.os.read",
                        side_effect=OSError("SECRET_READ_ERROR"),
                    ),
                ):
                    observed = ensure_watch_identity(watch_dir)
                self.assertIsNone(observed)
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
                descriptor_closed = True
            finally:
                if not descriptor_closed:
                    os.close(descriptor)

    def test_create_collision_returns_persisted_winner_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            watch_dir = Path(raw)
            marker = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
            winner = "fedcba98765432100123456789abcdef"
            marker.write_text(winner, encoding="ascii")
            with (
                patch(
                    "prefab_sentinel.bridge_watch_identity._read_marker",
                    side_effect=[FileNotFoundError, winner],
                ),
                patch(
                    "prefab_sentinel.bridge_watch_identity.os.open",
                    side_effect=FileExistsError,
                ),
            ):
                observed = ensure_watch_identity(watch_dir)
            self.assertEqual(winner, observed)
            self.assertEqual(winner, marker.read_text(encoding="ascii"))


class WatchIdentityObservationTests(unittest.TestCase):
    STATUS = {
        "schema_version": 1,
        "watch_identity": "b" * 32,
        "bridge_session_id": "c" * 32,
        "bridge_instance_id": "d" * 32,
        "updated_at_unix_ms": 10_000,
    }

    def _observe(
        self, payload: object, watch_identity: str | None, now_unix_ms: int
    ) -> WatchIdentityObservation:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            watch_dir = root / "watch"
            watch_dir.mkdir()
            if watch_identity is not None:
                (watch_dir / WATCH_IDENTITY_MARKER_FILENAME).write_text(
                    watch_identity, encoding="ascii"
                )
            status_path = root / BRIDGE_STATUS_RELATIVE_PATH
            status_path.parent.mkdir(parents=True)
            status_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            return observe_watch_identity(
                watch_dir=watch_dir, project_root=root, now_unix_ms=now_unix_ms
            )

    def test_freshness_boundaries_are_inclusive_only_through_5000_ms(self) -> None:
        expected = {14_999: "match", 15_000: "match", 15_001: "unavailable"}
        for now_unix_ms, state in expected.items():
            with self.subTest(now_unix_ms=now_unix_ms):
                observation = self._observe(self.STATUS, "b" * 32, now_unix_ms)
                self.assertEqual(state, observation.state)

    def test_fresh_unequal_identity_is_mismatch(self) -> None:
        observation = self._observe(self.STATUS, "a" * 32, 15_000)
        self.assertEqual(
            WatchIdentityObservation("mismatch", "identity_mismatch"), observation
        )

    def test_future_timestamp_and_unknown_field_are_unavailable(self) -> None:
        future = self._observe(self.STATUS, "b" * 32, 9_999)
        with_unknown = dict(self.STATUS, secret_field="SECRET")
        unknown = self._observe(with_unknown, "b" * 32, 15_000)
        self.assertEqual("unavailable", future.state)
        self.assertEqual("unavailable", unknown.state)

    def test_boolean_and_float_schema_versions_are_unavailable(self) -> None:
        for schema_version in (True, 1.0):
            with self.subTest(schema_version=schema_version):
                payload = dict(self.STATUS, schema_version=schema_version)
                self.assertEqual(
                    "unavailable",
                    self._observe(payload, "b" * 32, 15_000).state,
                )

    def test_symlink_status_is_rejected_without_no_follow_flag(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            watch_dir = root / "watch"
            watch_dir.mkdir()
            (watch_dir / WATCH_IDENTITY_MARKER_FILENAME).write_text("b" * 32, encoding="ascii")
            path = root / BRIDGE_STATUS_RELATIVE_PATH
            path.parent.mkdir(parents=True)
            target = path.with_name("real.json")
            target.write_text(json.dumps(self.STATUS), encoding="utf-8")
            path.symlink_to(target)
            module = __import__("prefab_sentinel.bridge_watch_identity", fromlist=["os"])
            original = getattr(module.os, "O_NOFOLLOW", None)
            try:
                module.os.O_NOFOLLOW = 0
                result = observe_watch_identity(
                    watch_dir=watch_dir, project_root=root, now_unix_ms=15_000
                )
            finally:
                if original is None:
                    delattr(module.os, "O_NOFOLLOW")
                else:
                    module.os.O_NOFOLLOW = original
        self.assertEqual("unavailable", result.state)

    def test_missing_fields_duplicate_keys_and_wrong_types_are_unavailable(self) -> None:
        missing = dict(self.STATUS)
        del missing["bridge_session_id"]
        duplicate = '{"schema_version":1,"schema_version":1,"watch_identity":"' + "b" * 32 + '","bridge_session_id":"' + "c" * 32 + '","bridge_instance_id":"' + "d" * 32 + '","updated_at_unix_ms":10000}'
        wrong_bool = dict(self.STATUS, updated_at_unix_ms=True)
        wrong_string = dict(self.STATUS, updated_at_unix_ms="10000")
        for payload in (missing, wrong_bool, wrong_string):
            with self.subTest(payload=payload):
                self.assertEqual("unavailable", self._observe(payload, "b" * 32, 15_000).state)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            watch_dir = root / "watch"
            watch_dir.mkdir()
            (watch_dir / WATCH_IDENTITY_MARKER_FILENAME).write_text("b" * 32, encoding="ascii")
            path = root / BRIDGE_STATUS_RELATIVE_PATH
            path.parent.mkdir(parents=True)
            path.write_text(duplicate, encoding="utf-8")
            result = observe_watch_identity(watch_dir=watch_dir, project_root=root, now_unix_ms=15_000)
        self.assertEqual("unavailable", result.state)

    def test_invalid_ids_are_unavailable(self) -> None:
        for field in ("watch_identity", "bridge_session_id", "bridge_instance_id"):
            with self.subTest(field=field):
                payload = dict(self.STATUS, **{field: "not-an-id"})
                self.assertEqual("unavailable", self._observe(payload, "b" * 32, 15_000).state)

    def test_status_size_boundaries(self) -> None:
        compact = json.dumps(self.STATUS, separators=(",", ":"))
        self.assertLess(len(compact.encode()), BRIDGE_STATUS_MAX_BYTES)
        for size, expected in ((4095, "match"), (4096, "match"), (4097, "unavailable")):
            payload = compact + (" " * (size - len(compact.encode())))
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                watch_dir = root / "watch"
                watch_dir.mkdir()
                (watch_dir / WATCH_IDENTITY_MARKER_FILENAME).write_text("b" * 32, encoding="ascii")
                path = root / BRIDGE_STATUS_RELATIVE_PATH
                path.parent.mkdir(parents=True)
                path.write_text(payload, encoding="ascii")
                result = observe_watch_identity(watch_dir=watch_dir, project_root=root, now_unix_ms=15_000)
            self.assertEqual(expected, result.state)

    def test_missing_status_and_missing_inputs_are_unavailable(self) -> None:
        self.assertEqual("unavailable", observe_watch_identity(watch_dir=None, project_root=Path("."), now_unix_ms=1).state)
        self.assertEqual("unavailable", observe_watch_identity(watch_dir=Path("."), project_root=None, now_unix_ms=1).state)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            watch_dir = root / "watch"
            watch_dir.mkdir()
            (watch_dir / WATCH_IDENTITY_MARKER_FILENAME).write_text("b" * 32, encoding="ascii")
            result = observe_watch_identity(watch_dir=watch_dir, project_root=root, now_unix_ms=1)
        self.assertEqual("unavailable", result.state)

    def test_symlink_fifo_and_read_oserror_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            watch_dir = root / "watch"
            watch_dir.mkdir()
            (watch_dir / WATCH_IDENTITY_MARKER_FILENAME).write_text("b" * 32, encoding="ascii")
            path = root / BRIDGE_STATUS_RELATIVE_PATH
            path.parent.mkdir(parents=True)
            target = path.with_name("real.json")
            target.write_text(json.dumps(self.STATUS), encoding="utf-8")
            path.symlink_to(target)
            self.assertEqual("unavailable", observe_watch_identity(watch_dir=watch_dir, project_root=root, now_unix_ms=15_000).state)
            path.unlink()
            os.mkfifo(path)
            self.assertEqual("unavailable", observe_watch_identity(watch_dir=watch_dir, project_root=root, now_unix_ms=15_000).state)
        with patch("prefab_sentinel.bridge_watch_identity.os.open", side_effect=OSError("nope")):
            result = observe_watch_identity(watch_dir=Path("."), project_root=Path("."), now_unix_ms=1)
        self.assertEqual("unavailable", result.state)

class WatchIdentityTrackerTests(unittest.TestCase):
    STATUS = {
        "schema_version": 1,
        "watch_identity": "b" * 32,
        "bridge_session_id": "c" * 32,
        "bridge_instance_id": "d" * 32,
        "updated_at_unix_ms": 10_000,
    }

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.watch_dir = self.root / "watch"
        self.watch_dir.mkdir()
        (self.watch_dir / WATCH_IDENTITY_MARKER_FILENAME).write_text(
            "b" * 32, encoding="ascii"
        )
        self.status_path = self.root / BRIDGE_STATUS_RELATIVE_PATH
        self.status_path.parent.mkdir(parents=True)
        self.tracker = bridge_watch_identity.WatchIdentityTracker()

    def _write_status(
        self,
        *,
        updated_at_unix_ms: int = 10_000,
        watch_identity: str = "b" * 32,
        schema_version: object = 1,
    ) -> None:
        payload = dict(
            self.STATUS,
            schema_version=schema_version,
            watch_identity=watch_identity,
            updated_at_unix_ms=updated_at_unix_ms,
        )
        self.status_path.write_text(
            json.dumps(payload, separators=(",", ":")),
            encoding="utf-8",
        )

    def _observe(self, now_unix_ms: int) -> WatchIdentityObservation:
        return self.tracker.observe(
            watch_dir=self.watch_dir,
            project_root=self.root,
            now_unix_ms=now_unix_ms,
        )

    def test_missing_after_fresh_is_transient_without_error_through_5000_ms(
        self,
    ) -> None:
        for elapsed_ms in (4_999, 5_000):
            with self.subTest(elapsed_ms=elapsed_ms):
                self.tracker = bridge_watch_identity.WatchIdentityTracker()
                self._write_status()
                self.assertEqual(
                    WatchIdentityObservation("match", "identity_match"),
                    self._observe(10_000),
                )
                self.status_path.unlink()
                with self.assertNoLogs(
                    "prefab_sentinel.bridge_watch_identity",
                    level="ERROR",
                ):
                    observation = self._observe(10_000 + elapsed_ms)
                self.assertEqual(
                    WatchIdentityObservation("unavailable", "status_transient"),
                    observation,
                )

    def test_missing_after_5000_ms_is_persistent_and_logs_once_per_outage(
        self,
    ) -> None:
        self._write_status()
        self.assertEqual("match", self._observe(10_000).state)
        self.status_path.unlink()

        with self.assertLogs(
            "prefab_sentinel.bridge_watch_identity",
            level="ERROR",
        ) as first_outage:
            first = self._observe(15_001)
            repeated = self._observe(15_002)

        self.assertEqual(
            (
                WatchIdentityObservation("unavailable", "status_missing"),
                WatchIdentityObservation("unavailable", "status_missing"),
                1,
            ),
            (first, repeated, len(first_outage.records)),
        )

        self._write_status(updated_at_unix_ms=20_000)
        self.assertEqual("match", self._observe(20_000).state)
        self.status_path.unlink()
        with self.assertLogs(
            "prefab_sentinel.bridge_watch_identity",
            level="ERROR",
        ) as second_outage:
            recovered_outage = self._observe(25_001)
        self.assertEqual(
            (
                WatchIdentityObservation("unavailable", "status_missing"),
                1,
            ),
            (recovered_outage, len(second_outage.records)),
        )

    def test_initial_missing_status_has_private_missing_reason_and_log(self) -> None:
        with self.assertLogs(
            "prefab_sentinel.bridge_watch_identity",
            level="ERROR",
        ) as logs:
            observation = self._observe(1)

        self.assertEqual(
            (
                WatchIdentityObservation("unavailable", "status_missing"),
                ["Bridge status artifact is missing"],
            ),
            (
                observation,
                [record.getMessage() for record in logs.records],
            ),
        )

    def test_fresh_mismatch_starts_the_transient_window(self) -> None:
        self._write_status(watch_identity="a" * 32)
        self.assertEqual(
            WatchIdentityObservation("mismatch", "identity_mismatch"),
            self._observe(10_000),
        )
        self.status_path.unlink()
        with self.assertNoLogs(
            "prefab_sentinel.bridge_watch_identity",
            level="ERROR",
        ):
            observation = self._observe(15_000)
        self.assertEqual(
            WatchIdentityObservation("unavailable", "status_transient"),
            observation,
        )

    def test_reset_discards_prior_freshness(self) -> None:
        self._write_status()
        self.assertEqual("match", self._observe(10_000).state)
        self.tracker.reset()
        self.status_path.unlink()

        with self.assertLogs(
            "prefab_sentinel.bridge_watch_identity",
            level="ERROR",
        ):
            observation = self._observe(10_001)

        self.assertEqual(
            WatchIdentityObservation("unavailable", "status_missing"),
            observation,
        )

    def test_stale_and_invalid_status_are_persistent(self) -> None:
        self._write_status(updated_at_unix_ms=10_000)
        with self.assertLogs(
            "prefab_sentinel.bridge_watch_identity",
            level="ERROR",
        ):
            stale = self._observe(15_001)

        self.tracker = bridge_watch_identity.WatchIdentityTracker()
        self._write_status(schema_version=2)
        with self.assertLogs(
            "prefab_sentinel.bridge_watch_identity",
            level="ERROR",
        ):
            invalid = self._observe(10_000)

        for observation in (stale, invalid):
            with self.subTest(reason=observation.reason):
                self.assertEqual("unavailable", observation.state)
                self.assertNotEqual("status_transient", observation.reason)

    def test_permission_failure_has_private_read_reason_and_exception_log(
        self,
    ) -> None:
        secret = "ISSUE194_PRIVATE_PERMISSION"
        with (
            patch.object(
                bridge_watch_identity,
                "_read_status",
                side_effect=PermissionError(secret),
            ),
            self.assertLogs(
                "prefab_sentinel.bridge_watch_identity",
                level="ERROR",
            ) as logs,
        ):
            observation = self._observe(10_000)

        self.assertEqual(
            (
                WatchIdentityObservation("unavailable", "status_read_failed"),
                ["Unable to read bridge status artifact"],
            ),
            (
                observation,
                [record.getMessage() for record in logs.records],
            ),
        )
        self.assertIn(secret, "\n".join(logs.output))

    def test_invalid_schema_shape_and_size_share_private_invalid_reason(
        self,
    ) -> None:
        valid = dict(self.STATUS)
        invalid_schema = dict(valid, schema_version=2)
        invalid_shape = dict(valid)
        invalid_shape.pop("bridge_instance_id")
        cases = (
            (
                "schema",
                json.dumps(invalid_schema, separators=(",", ":")).encode("utf-8"),
            ),
            (
                "shape",
                json.dumps(invalid_shape, separators=(",", ":")).encode("utf-8"),
            ),
            ("size", b"x" * (BRIDGE_STATUS_MAX_BYTES + 1)),
        )

        for label, payload in cases:
            with self.subTest(label=label):
                self.tracker = bridge_watch_identity.WatchIdentityTracker()
                self.status_path.write_bytes(payload)
                with self.assertLogs(
                    "prefab_sentinel.bridge_watch_identity",
                    level="ERROR",
                ) as logs:
                    observation = self._observe(10_000)
                self.assertEqual(
                    (
                        WatchIdentityObservation(
                            "unavailable",
                            "status_invalid",
                        ),
                        ["Bridge status artifact is invalid"],
                    ),
                    (
                        observation,
                        [record.getMessage() for record in logs.records],
                    ),
                )

    def test_marker_permission_error_is_coalesced_and_fresh_or_reset_rearms(
        self,
    ) -> None:
        secret = "ISSUE194_PRIVATE_MARKER_PERMISSION"
        self._write_status()

        def observe_outage() -> tuple[WatchIdentityObservation, WatchIdentityObservation, list[str]]:
            with (
                patch.object(
                    bridge_watch_identity,
                    "_read_marker",
                    side_effect=PermissionError(secret),
                ),
                self.assertLogs(
                    "prefab_sentinel.bridge_watch_identity",
                    level="ERROR",
                ) as logs,
            ):
                first = self._observe(10_000)
                repeated = self._observe(10_001)
            return first, repeated, [record.getMessage() for record in logs.records]

        first_outage = observe_outage()
        self.assertEqual(
            (
                WatchIdentityObservation("unavailable", "marker_read_failed"),
                WatchIdentityObservation("unavailable", "marker_read_failed"),
                ["Unable to read watch identity marker"],
            ),
            first_outage,
        )

        self.assertEqual("match", self._observe(10_000).state)
        second_outage = observe_outage()
        self.assertEqual(["Unable to read watch identity marker"], second_outage[2])

        self.tracker.reset()
        reset_outage = observe_outage()
        self.assertEqual(["Unable to read watch identity marker"], reset_outage[2])

    def test_inclusive_transient_boundary_recovers_to_fresh_match(self) -> None:
        self._write_status()
        fresh_before = self._observe(10_000)
        self.status_path.unlink()
        with self.assertNoLogs(
            "prefab_sentinel.bridge_watch_identity",
            level="ERROR",
        ):
            transient = self._observe(15_000)

        self._write_status(updated_at_unix_ms=15_000)
        fresh_after = self._observe(15_000)

        self.assertEqual(
            (
                WatchIdentityObservation("match", "identity_match"),
                WatchIdentityObservation("unavailable", "status_transient"),
                WatchIdentityObservation("match", "identity_match"),
            ),
            (fresh_before, transient, fresh_after),
        )

    def test_persistent_outage_does_not_regress_to_transient_before_recovery(
        self,
    ) -> None:
        self._write_status()
        self.assertEqual("match", self._observe(10_000).state)

        self._write_status(schema_version=2)
        with self.assertLogs(
            "prefab_sentinel.bridge_watch_identity",
            level="ERROR",
        ):
            invalid = self._observe(10_001)
        self.status_path.unlink()
        with self.assertNoLogs(
            "prefab_sentinel.bridge_watch_identity",
            level="ERROR",
        ):
            missing = self._observe(10_002)

        self.assertEqual(
            (
                WatchIdentityObservation("unavailable", "status_invalid"),
                WatchIdentityObservation("unavailable", "status_missing"),
            ),
            (invalid, missing),
        )


if __name__ == "__main__":
    unittest.main()
