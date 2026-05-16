"""Shared test helpers for Editor Bridge tests.

Provides:
* ``write_file``  – create a file with parent directory creation.
* ``EditorBridgeResponder`` – a background thread that watches a
  temporary directory for ``*.request.json`` files and writes a
  caller-supplied response envelope as ``*.response.json``.  The
  responder is the editor-bridge equivalent of a Unity Editor in the
  unit-test surface — the production code routes one request through
  the watch directory and the responder fakes the response side of the
  round trip without touching Unity.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def write_file(path: Path, content: str) -> None:
    """Write *content* to *path*, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class EditorBridgeResponder:
    """Background thread that fakes the Unity-side editor-bridge responder.

    Polls *watch_dir* for ``<id>.request.json`` files; when one appears,
    invokes *response_builder* with the parsed request payload and
    writes the returned envelope to ``<id>.response.json`` atomically
    (``.tmp`` then rename).  Designed to be used as a context manager.

    The responder is the boundary the runtime-validation and patch-CLI
    T1 tests stub: every production code path under test writes a
    request file to the watch directory and polls for the matching
    response file; the responder closes that loop without invoking
    Unity.
    """

    def __init__(
        self,
        watch_dir: Path,
        response_builder: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        poll_interval: float = 0.05,
    ) -> None:
        self._watch_dir = watch_dir
        self._response_builder = response_builder
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.observed_requests: list[dict[str, Any]] = []

    def __enter__(self) -> EditorBridgeResponder:
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        seen: set[str] = set()
        while not self._stop_event.is_set():
            try:
                entries = list(self._watch_dir.iterdir())
            except FileNotFoundError:
                time.sleep(self._poll_interval)
                continue
            for entry in entries:
                if entry.name in seen or not entry.name.endswith(".request.json"):
                    continue
                request_id = entry.name[: -len(".request.json")]
                response_file = self._watch_dir / f"{request_id}.response.json"
                if response_file.exists():
                    continue
                try:
                    request_payload = json.loads(entry.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    # Production code rewrites the request file atomically;
                    # a partial-read here means we'll retry next tick.
                    continue
                self.observed_requests.append(request_payload)
                response_payload = self._response_builder(request_payload)
                tmp_file = self._watch_dir / f"{request_id}.response.json.tmp"
                tmp_file.write_text(
                    json.dumps(response_payload, ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp_file.rename(response_file)
                seen.add(entry.name)
            time.sleep(self._poll_interval)
