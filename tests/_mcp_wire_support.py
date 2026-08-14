"""Real-process helpers for MCP CLI wire-contract tests."""

from __future__ import annotations

import http.client
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import unittest
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, cast

MCP_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-11-25"

_PROCESS_TIMEOUT_SECONDS = 15.0
_SHUTDOWN_TIMEOUT_SECONDS = 5.0
_HTTP_TIMEOUT_SECONDS = 1.0
_READINESS_TIMEOUT_SECONDS = 10.0


_STREAM_EOF = object()


@dataclass
class CLIProcess:
    """A child process with one lifetime owner for each captured read pipe."""

    process: subprocess.Popen[str]
    _stdout_lines: queue.Queue[object] = field(init=False, repr=False)
    _stdout_parts: list[str] = field(init=False, repr=False)
    _stderr_parts: list[str] = field(init=False, repr=False)
    _stdout_error: BaseException | None = field(init=False, default=None, repr=False)
    _stderr_error: BaseException | None = field(init=False, default=None, repr=False)
    _stdout_reader: threading.Thread = field(init=False, repr=False)
    _stderr_reader: threading.Thread = field(init=False, repr=False)
    _reaped: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self._stdout_lines = queue.Queue()
        self._stdout_parts = []
        self._stderr_parts = []
        self._stdout_reader = threading.Thread(
            target=self._drain_stdout,
            name=f"mcp-stdout-{self.process.pid}",
        )
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr,
            name=f"mcp-stderr-{self.process.pid}",
        )
        self._stdout_reader.start()
        self._stderr_reader.start()

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    @property
    def stdout(self) -> str:
        return "".join(self._stdout_parts)

    @property
    def stderr(self) -> str:
        return "".join(self._stderr_parts)

    @property
    def reader_threads_alive(self) -> bool:
        return self._stdout_reader.is_alive() or self._stderr_reader.is_alive()

    def _drain_stdout(self) -> None:
        stream = self.process.stdout
        if stream is None:
            error = AssertionError("MCP CLI stdout is not piped")
            self._stdout_error = error
            self._stdout_lines.put(error)
            self._stdout_lines.put(_STREAM_EOF)
            return
        try:
            for line in stream:
                self._stdout_parts.append(line)
                self._stdout_lines.put(line)
        except BaseException as exc:  # Preserve pipe-reader failures for the test thread.
            self._stdout_error = exc
            self._stdout_lines.put(exc)
        finally:
            self._stdout_lines.put(_STREAM_EOF)

    def _drain_stderr(self) -> None:
        stream = self.process.stderr
        if stream is None:
            self._stderr_error = AssertionError("MCP CLI stderr is not piped")
            return
        try:
            for line in stream:
                self._stderr_parts.append(line)
        except BaseException as exc:  # Preserve pipe-reader failures for cleanup diagnostics.
            self._stderr_error = exc

    def _close_stdin(self) -> None:
        stdin = self.process.stdin
        if stdin is None or stdin.closed:
            return
        with suppress(BrokenPipeError):
            stdin.close()

    def _join_readers(self, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        for reader in (self._stdout_reader, self._stderr_reader):
            reader.join(timeout=max(0.0, deadline - time.monotonic()))
        if self.reader_threads_alive:
            raise AssertionError("MCP CLI pipe readers did not stop after child exit")
        if self._stdout_error is not None:
            raise AssertionError(f"stdout reader failed: {self._stdout_error}") from self._stdout_error
        if self._stderr_error is not None:
            raise AssertionError(f"stderr reader failed: {self._stderr_error}") from self._stderr_error

    def _finish_reap(self) -> None:
        self._join_readers(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        self._reaped = True

    def communicate(
        self,
        input_text: str | None = None,
        *,
        timeout: float = _PROCESS_TIMEOUT_SECONDS,
    ) -> tuple[str, str]:
        if self._reaped:
            return self.stdout, self.stderr
        stdin = self.process.stdin
        if input_text and stdin is None:
            raise AssertionError("MCP CLI stdin is not piped")
        if input_text and stdin is not None:
            stdin.write(input_text)
            stdin.flush()
        self._close_stdin()
        self.process.wait(timeout=timeout)
        self._finish_reap()
        return self.stdout, self.stderr

    def write_json_line(self, request: Mapping[str, object]) -> None:
        stdin = self.process.stdin
        if stdin is None or stdin.closed:
            raise AssertionError("MCP CLI stdin is not piped")
        stdin.write(json.dumps(request, separators=(",", ":")))
        stdin.write("\n")
        stdin.flush()

    def read_json_line(
        self,
        *,
        timeout: float = _PROCESS_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        try:
            observed = self._stdout_lines.get(timeout=timeout)
        except queue.Empty as exc:
            raise AssertionError("timed out waiting for one MCP stdio response") from exc
        if observed is _STREAM_EOF:
            self._stdout_lines.put(_STREAM_EOF)
            raise AssertionError(f"MCP CLI stdout closed before a response; returncode={self.process.poll()}")
        if isinstance(observed, BaseException):
            raise AssertionError(f"stdout reader failed: {observed}") from observed
        if not isinstance(observed, str):
            raise AssertionError(f"stdout reader returned an unexpected value: {observed!r}")
        decoded = json.loads(observed)
        if not isinstance(decoded, dict):
            raise AssertionError(f"expected a JSON object response, observed {decoded!r}")
        return cast(dict[str, Any], decoded)

    def request_json_line(
        self,
        request: Mapping[str, object],
        *,
        timeout: float = _PROCESS_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        self.write_json_line(request)
        return self.read_json_line(timeout=timeout)

    def stop(self) -> None:
        if self._reaped:
            return
        self._close_stdin()
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        self._finish_reap()


@contextmanager
def running_mcp_cli(
    *arguments: str,
    pipe_stdin: bool = False,
) -> Iterator[CLIProcess]:
    """Launch the installed module and always reap it with useful failure output."""

    environment = os.environ.copy()
    environment.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)
    environment.pop("UNITYTOOL_UNITY_PROJECT_PATH", None)
    environment["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "prefab_sentinel.mcp_server", *arguments],
        stdin=subprocess.PIPE if pipe_stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    child = CLIProcess(process)
    error: Exception | None = None
    cleanup_error: Exception | None = None
    try:
        yield child
    except Exception as exc:  # Assertions are re-raised with child diagnostics.
        error = exc
    finally:
        try:
            child.stop()
        except Exception as exc:
            cleanup_error = exc

    if error is not None:
        cleanup_note = f"\ncleanup error: {cleanup_error}" if cleanup_error else ""
        raise AssertionError(f"{error}{cleanup_note}\nchild stderr:\n{child.stderr or '<empty>'}") from error
    if cleanup_error is not None:
        raise AssertionError(
            f"child cleanup failed: {cleanup_error}\nchild stderr:\n{child.stderr or '<empty>'}"
        ) from cleanup_error


def reserve_loopback_port() -> int:
    """Ask the OS for an unused IPv4 loopback port and release it for Uvicorn."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return cast(int, reservation.getsockname()[1])


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: dict[str, str]
    body: bytes

    def json_object(self) -> dict[str, Any]:
        decoded = json.loads(self.body)
        if not isinstance(decoded, dict):
            raise AssertionError(f"expected a JSON object, observed {decoded!r}")
        return cast(dict[str, Any], decoded)


def http_request(
    port: int,
    method: str,
    path: str,
    *,
    body: Mapping[str, object] | bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> HTTPResult:
    """Send one bounded standard-library HTTP request."""

    connection = http.client.HTTPConnection(
        "127.0.0.1",
        port,
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    request_headers = dict(headers or {})
    encoded_body: bytes | None
    if body is None or isinstance(body, bytes):
        encoded_body = body
    else:
        encoded_body = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("content-type", "application/json")
    try:
        connection.request(method, path, body=encoded_body, headers=request_headers)
        response = connection.getresponse()
        response_body = response.read()
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        return HTTPResult(response.status, response_headers, response_body)
    finally:
        connection.close()


def wait_for_http_ready(
    child: CLIProcess,
    port: int,
    *,
    timeout: float = _READINESS_TIMEOUT_SECONDS,
) -> None:
    """Poll an exact product discovery exchange until the launched child is ready."""

    request_id = f"readiness-{uuid.uuid4().hex}"
    request = modern_request("server/discover", request_id=request_id)
    headers = modern_headers("server/discover")
    deadline = time.monotonic() + timeout
    last_observation = "no response"
    while time.monotonic() < deadline:
        if child.process.poll() is not None:
            raise AssertionError(f"HTTP CLI exited during readiness with code {child.process.returncode}")
        try:
            response = http_request(
                port,
                "POST",
                "/mcp",
                body=request,
                headers=headers,
            )
        except (OSError, http.client.HTTPException) as exc:
            last_observation = repr(exc)
        else:
            content_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
            if response.status != 200 or content_type != "application/json":
                last_observation = f"status={response.status}, content-type={content_type!r}"
            else:
                try:
                    payload = response.json_object()
                except (ValueError, AssertionError) as exc:
                    last_observation = f"invalid JSON-RPC body: {exc}"
                else:
                    result = payload.get("result")
                    meta = result.get("_meta") if isinstance(result, Mapping) else None
                    server_info = meta.get("io.modelcontextprotocol/serverInfo") if isinstance(meta, Mapping) else None
                    is_product_discovery = (
                        payload.get("jsonrpc") == "2.0"
                        and payload.get("id") == request_id
                        and "method" not in payload
                        and ("result" in payload, "error" in payload) == (True, False)
                        and isinstance(result, Mapping)
                        and result.get("supportedVersions") == [MCP_PROTOCOL_VERSION]
                        and result.get("capabilities") == {"tools": {"listChanged": False}}
                        and isinstance(server_info, Mapping)
                        and server_info.get("name") == "prefab-sentinel"
                    )
                    if is_product_discovery:
                        if child.process.poll() is not None:
                            raise AssertionError(
                                f"HTTP CLI exited after readiness with code {child.process.returncode}"
                            )
                        return
                    last_observation = f"unrecognized discovery response: {payload!r}"
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.05, remaining))
    raise AssertionError(f"HTTP CLI did not become ready before timeout; last observation: {last_observation}")


def assert_jsonrpc_result(
    test: unittest.TestCase,
    payload: Mapping[str, Any],
    *,
    request_id: int | str,
) -> dict[str, Any]:
    """Pin the JSON-RPC 2.0 success response envelope."""

    test.assertEqual("2.0", payload.get("jsonrpc"))
    test.assertEqual(request_id, payload.get("id"))
    test.assertNotIn("method", payload)
    test.assertEqual((True, False), ("result" in payload, "error" in payload))
    result = payload["result"]
    test.assertIsInstance(result, dict)
    return cast(dict[str, Any], result)


def assert_jsonrpc_error(
    test: unittest.TestCase,
    payload: Mapping[str, Any],
    *,
    request_id: int | str,
    code: int,
    message: str,
) -> dict[str, Any]:
    """Pin the JSON-RPC 2.0 error response envelope."""

    test.assertEqual("2.0", payload.get("jsonrpc"))
    test.assertEqual(request_id, payload.get("id"))
    test.assertNotIn("method", payload)
    test.assertEqual((False, True), ("result" in payload, "error" in payload))
    error = payload["error"]
    test.assertIsInstance(error, dict)
    typed_error = cast(dict[str, Any], error)
    test.assertEqual(code, typed_error.get("code"))
    test.assertEqual(message, typed_error.get("message"))
    return typed_error


def modern_meta(version: str = MCP_PROTOCOL_VERSION) -> dict[str, object]:
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {
            "name": "transport-tests",
            "version": "1",
        },
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def modern_request(
    method: str,
    *,
    request_id: int | str | None = 1,
    version: str = MCP_PROTOCOL_VERSION,
    params: Mapping[str, object] | None = None,
) -> dict[str, object]:
    request_params = {"_meta": modern_meta(version), **dict(params or {})}
    request: dict[str, object] = {
        "jsonrpc": "2.0",
        "method": method,
        "params": request_params,
    }
    if request_id is not None:
        request["id"] = request_id
    return request


def modern_headers(
    method: str,
    *,
    version: str = MCP_PROTOCOL_VERSION,
    name: str | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "mcp-protocol-version": version,
        "mcp-method": method,
    }
    if name is not None:
        headers["mcp-name"] = name
    if extra is not None:
        headers.update(extra)
    return headers
