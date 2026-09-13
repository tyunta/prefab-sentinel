"""Bounded, regular-file-only JSON reads for Editor Bridge responses."""

from __future__ import annotations

import math
import os
import stat
from pathlib import Path

from prefab_sentinel.bridge_constants import BRIDGE_RESPONSE_MAX_BYTES
from prefab_sentinel.json_io import load_json


class BridgeResponseReadError(Exception):
    """The response file cannot be accepted as a bounded JSON value."""


def _reject_non_standard_number(_: str) -> None:
    raise ValueError("Non-standard JSON numbers are not accepted.")


def _parse_finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("Non-finite JSON numbers are not accepted.")
    return value


def read_bridge_response_file(path: Path) -> object:
    """Read one bounded UTF-8 JSON value from a regular response file."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    try:
        fd = os.open(path, flags)
        file_status = os.fstat(fd)
        if not stat.S_ISREG(file_status.st_mode):
            raise BridgeResponseReadError("Bridge response must be a regular file.")
        if file_status.st_size > BRIDGE_RESPONSE_MAX_BYTES:
            raise BridgeResponseReadError("Bridge response exceeds the size limit.")

        chunks = bytearray()
        while len(chunks) <= BRIDGE_RESPONSE_MAX_BYTES:
            chunk = os.read(
                fd,
                min(
                    64 * 1024,
                    BRIDGE_RESPONSE_MAX_BYTES + 1 - len(chunks),
                ),
            )
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > BRIDGE_RESPONSE_MAX_BYTES:
            raise BridgeResponseReadError("Bridge response exceeds the size limit.")

        return load_json(
            bytes(chunks).decode("utf-8"),
            parse_constant=_reject_non_standard_number,
            parse_float=_parse_finite_float,
        )
    except BridgeResponseReadError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise BridgeResponseReadError(
            "Bridge response could not be read as JSON."
        ) from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError as exc:
                raise BridgeResponseReadError(
                    "Bridge response could not be read as JSON."
                ) from exc
