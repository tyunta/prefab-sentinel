from __future__ import annotations

import json
import logging
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

WATCH_IDENTITY_MARKER_FILENAME = ".prefab-sentinel-watch-identity"
BRIDGE_STATUS_RELATIVE_PATH = Path("Library/PrefabSentinel/bridge-status-v1.json")
BRIDGE_STATUS_SCHEMA_VERSION = 1
BRIDGE_STATUS_MAX_BYTES = 4096
BRIDGE_STATUS_FRESHNESS_MS = 5000

WatchIdentityState = Literal["match", "mismatch", "unavailable"]

logger = logging.getLogger(__name__)
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STATUS_FIELDS = {
    "schema_version",
    "watch_identity",
    "bridge_session_id",
    "bridge_instance_id",
    "updated_at_unix_ms",
}


@dataclass(frozen=True)
class WatchIdentityObservation:
    state: WatchIdentityState
    reason: str

@dataclass(frozen=True)
class _WatchIdentityMarkerProbe:
    identity: str | None
    reason: str
    error: BaseException | None = None


def _valid_identity(value: object) -> bool:
    return isinstance(value, str) and _ID_PATTERN.fullmatch(value) is not None


def _read_marker(marker_path: Path) -> str | None:
    path_info = os.lstat(marker_path)
    if (
        stat.S_ISLNK(path_info.st_mode)
        or not stat.S_ISREG(path_info.st_mode)
        or path_info.st_size != 32
    ):
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = os.open(marker_path, flags)
    try:
        descriptor_info = os.fstat(fd)
        if (
            not stat.S_ISREG(descriptor_info.st_mode)
            or descriptor_info.st_size != 32
        ):
            return None
        value = os.read(fd, 33)
    finally:
        os.close(fd)
    if len(value) != 32:
        return None
    decoded = value.decode("ascii")
    if _ID_PATTERN.fullmatch(decoded) is None:
        return None
    return decoded


def _probe_watch_identity(watch_dir: Path) -> _WatchIdentityMarkerProbe:
    marker_path = watch_dir / WATCH_IDENTITY_MARKER_FILENAME
    try:
        identity = _read_marker(marker_path)
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError) as exc:
        return _WatchIdentityMarkerProbe(None, "marker_read_failed", exc)
    else:
        reason = "identity_ready" if identity is not None else "marker_invalid"
        return _WatchIdentityMarkerProbe(identity, reason)

    try:
        fd = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            identity_bytes = uuid.uuid4().hex.encode("ascii")
            stream.write(identity_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        return _WatchIdentityMarkerProbe(
            identity_bytes.decode("ascii"),
            "identity_ready",
        )
    except FileExistsError:
        try:
            identity = _read_marker(marker_path)
        except (OSError, UnicodeError) as exc:
            return _WatchIdentityMarkerProbe(None, "marker_read_failed", exc)
        reason = "identity_ready" if identity is not None else "marker_invalid"
        return _WatchIdentityMarkerProbe(identity, reason)
    except (OSError, UnicodeError) as exc:
        return _WatchIdentityMarkerProbe(None, "marker_write_failed", exc)


def ensure_watch_identity(watch_dir: Path) -> str | None:
    """Ensure a marker for standalone callers, logging a private failure."""
    probe = _probe_watch_identity(watch_dir)
    if probe.error is not None:
        message = (
            "Unable to read watch identity marker"
            if probe.reason == "marker_read_failed"
            else "Unable to create watch identity marker"
        )
        logger.error(message, exc_info=probe.error)
    return probe.identity


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _read_status(status_path: Path) -> dict[str, object] | None:
    path_info = os.lstat(status_path)
    if stat.S_ISLNK(path_info.st_mode) or not stat.S_ISREG(path_info.st_mode):
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = os.open(status_path, flags)
    try:
        descriptor_info = os.fstat(fd)
        if not stat.S_ISREG(descriptor_info.st_mode):
            return None
        payload = os.read(fd, BRIDGE_STATUS_MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(payload) > BRIDGE_STATUS_MAX_BYTES:
        return None
    decoded = payload.decode("utf-8")
    value = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(value, dict) or set(value) != _STATUS_FIELDS:
        return None
    if type(value["schema_version"]) is not int:
        return None
    if value["schema_version"] != BRIDGE_STATUS_SCHEMA_VERSION:
        return None
    if not all(_valid_identity(value[field]) for field in ("watch_identity", "bridge_session_id", "bridge_instance_id")):
        return None
    if type(value["updated_at_unix_ms"]) is not int:
        return None
    return value


class WatchIdentityTracker:
    """Track fresh Bridge status observations and coalesce outage logging."""

    def __init__(self) -> None:
        self._last_fresh_unix_ms: int | None = None
        self._outage_logged = False

    def reset(self) -> None:
        self._last_fresh_unix_ms = None
        self._outage_logged = False

    def _persistent_unavailable(
        self,
        reason: str,
        *,
        log_message: str,
        error: BaseException | None = None,
    ) -> WatchIdentityObservation:
        if not self._outage_logged:
            logger.error(log_message, exc_info=error)
        self._outage_logged = True
        return WatchIdentityObservation("unavailable", reason)

    def _missing_status(self, now_unix_ms: int) -> WatchIdentityObservation:
        last_fresh = self._last_fresh_unix_ms
        if (
            not self._outage_logged
            and last_fresh is not None
            and 0 <= now_unix_ms - last_fresh <= BRIDGE_STATUS_FRESHNESS_MS
        ):
            return WatchIdentityObservation("unavailable", "status_transient")
        return self._persistent_unavailable(
            "status_missing",
            log_message="Bridge status artifact is missing",
        )

    def observe(
        self,
        *,
        watch_dir: Path | None,
        project_root: Path | None,
        now_unix_ms: int,
    ) -> WatchIdentityObservation:
        if watch_dir is None or project_root is None:
            return self._persistent_unavailable(
                "status_unavailable",
                log_message="Bridge status observation inputs are unavailable",
            )

        marker_probe = _probe_watch_identity(watch_dir)
        if marker_probe.identity is None:
            marker_messages = {
                "marker_invalid": "Watch identity marker is invalid",
                "marker_read_failed": "Unable to read watch identity marker",
                "marker_write_failed": "Unable to create watch identity marker",
            }
            return self._persistent_unavailable(
                marker_probe.reason,
                log_message=marker_messages[marker_probe.reason],
                error=marker_probe.error,
            )
        marker_identity = marker_probe.identity

        try:
            status = _read_status(project_root / BRIDGE_STATUS_RELATIVE_PATH)
        except FileNotFoundError:
            return self._missing_status(now_unix_ms)
        except (json.JSONDecodeError, UnicodeError, ValueError, TypeError):
            return self._persistent_unavailable(
                "status_invalid",
                log_message="Bridge status artifact is invalid",
            )
        except OSError as exc:
            return self._persistent_unavailable(
                "status_read_failed",
                log_message="Unable to read bridge status artifact",
                error=exc,
            )
        if status is None:
            return self._persistent_unavailable(
                "status_invalid",
                log_message="Bridge status artifact is invalid",
            )

        updated_at = cast(int, status["updated_at_unix_ms"])
        age = now_unix_ms - updated_at
        if age < 0 or age > BRIDGE_STATUS_FRESHNESS_MS:
            return self._persistent_unavailable(
                "status_stale",
                log_message="Bridge status artifact is stale",
            )

        state: WatchIdentityState = (
            "match" if status["watch_identity"] == marker_identity else "mismatch"
        )
        self._last_fresh_unix_ms = now_unix_ms
        self._outage_logged = False
        reason = "identity_match" if state == "match" else "identity_mismatch"
        return WatchIdentityObservation(state, reason)


def observe_watch_identity(
    *,
    watch_dir: Path | None,
    project_root: Path | None,
    now_unix_ms: int,
) -> WatchIdentityObservation:
    """Perform a one-shot observation without carrying session freshness."""
    return WatchIdentityTracker().observe(
        watch_dir=watch_dir,
        project_root=project_root,
        now_unix_ms=now_unix_ms,
    )
