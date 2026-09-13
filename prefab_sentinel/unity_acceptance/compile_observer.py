from __future__ import annotations

import codecs
import hashlib
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path

from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.unity_acceptance.model import CompileBaseline, CompileObservation
from prefab_sentinel.wsl_compat import to_wsl_path

_DLL_RELATIVE_PATH = Path("Library/ScriptAssemblies/PrefabSentinel.Editor.dll")
_POLL_INTERVAL_SECONDS = 1.0
_LOG_HEADER_LIMIT_BYTES = 65536
_LOG_APPEND_CHUNK_BYTES = 65536
_COMPILE_COMPLETION_MARKER = (
    "Reloading assemblies after finishing script compilation."
)
# The v296 acceptance log's longest measured line was 1,565 bytes.
_LOG_EVENT_LINE_LIMIT_CHARS = 4096
_COMPILE_EVENT_PATTERN = re.compile(
    r"(?P<additional>\*\*\* Tundra requires additional run\b)"
    r'|(?P<generation>Starting:[^\r\n]*?\bbee_backend(?:\.exe)?\b[^\r\n]*?'
    r'--dagfile="(?P<graph>[^"\r\n]+)"[^\r\n]*?\bScriptAssemblies\b)'
    r"|(?P<csc>\bCsc\b[^\r\n]*?\bPrefabSentinel\.Editor\.dll\b)"
    r"|(?P<copy>\bCopyFiles\b[^\r\n]*?\bLibrary[\\/]ScriptAssemblies[\\/]"
    r"PrefabSentinel\.Editor\.dll\b)"
    r"|(?P<build>\*\*\* Tundra build success\b)"
    rf"|(?P<reload>{re.escape(_COMPILE_COMPLETION_MARKER)})"
    r"|(?P<error>\berror (?P<error_code>CS\d{4})\b)"
)
_PENDING_COMPILER_ERROR_PATTERN = re.compile(
    r"\berror (?P<error_code>CS\d{4})(?=\W)"
)
_SUPERSEDING_EVIDENCE = ("csc", "copy", "build", "reload")


class _CompileLogParser:
    """Correlate one failed Bee generation with one exact same-graph rerun."""

    def __init__(self) -> None:
        self._pending_text = ""
        self._pending_error_ends: set[int] = set()
        self._current_graph: str | None = None
        self._current_errors: list[str] = []
        self._unscoped_errors: list[str] = []
        self._awaiting_graph: str | None = None
        self._superseded_errors: list[str] = []
        self._superseding = False
        self._evidence_index = 0
        self._saw_reload = False
        self.terminal_failure = False
        self.log_evidence_lost = False

    @property
    def compiler_errors(self) -> tuple[str, ...]:
        return tuple(self._unscoped_errors + self._current_errors)

    @property
    def superseded_compiler_errors(self) -> tuple[str, ...]:
        return tuple(self._superseded_errors)

    @property
    def compile_completed(self) -> bool:
        if self._superseded_errors:
            return (
                self._superseding
                and self._evidence_index == len(_SUPERSEDING_EVIDENCE)
                and not self.compiler_errors
                and not self._has_unresolved_pending_text
            )
        return (
            self._saw_reload
            and not self.compiler_errors
            and not self._has_unresolved_pending_text
        )

    @property
    def _has_unresolved_pending_text(self) -> bool:
        return bool(self._pending_text.strip()) and (
            self._pending_text != _COMPILE_COMPLETION_MARKER
        )

    def feed(self, decoded_chunk: str) -> None:
        self._pending_text += decoded_chunk
        while "\n" in self._pending_text:
            line, self._pending_text = self._pending_text.split("\n", 1)
            if len(line) > _LOG_EVENT_LINE_LIMIT_CHARS:
                self.log_evidence_lost = True
                return
            self._consume_line(line, committed_error_ends=self._pending_error_ends)
            self._pending_error_ends.clear()
        if len(self._pending_text) > _LOG_EVENT_LINE_LIMIT_CHARS:
            self.log_evidence_lost = True

    def commit_available_input(self) -> None:
        """Preserve the prior exact-marker-at-EOF contract without partial tokens."""
        for match in _PENDING_COMPILER_ERROR_PATTERN.finditer(self._pending_text):
            error_end = match.end()
            if error_end in self._pending_error_ends:
                continue
            self._pending_error_ends.add(error_end)
            self._compiler_error(f"error {match.group('error_code')}")
        if self._pending_text == _COMPILE_COMPLETION_MARKER:
            self._saw_reload = True
            self._advance_evidence("reload")

    def _consume_line(
        self,
        line: str,
        *,
        committed_error_ends: set[int] | None = None,
    ) -> None:
        for match in _COMPILE_EVENT_PATTERN.finditer(line):
            if match.group("additional") is not None:
                self._additional_run()
            elif match.group("generation") is not None:
                graph = match.group("graph")
                assert graph is not None
                self._generation_started(graph)
            elif match.group("error") is not None:
                if (
                    committed_error_ends is not None
                    and match.end() in committed_error_ends
                ):
                    continue
                code = match.group("error_code")
                assert code is not None
                self._compiler_error(f"error {code}")
            elif match.group("reload") is not None:
                self._saw_reload = True
                self._advance_evidence("reload")
            elif match.group("csc") is not None:
                self._advance_evidence("csc")
            elif match.group("copy") is not None:
                self._advance_evidence("copy")
            elif match.group("build") is not None:
                self._advance_evidence("build")

    def _additional_run(self) -> None:
        if self._current_graph is not None and self._current_errors:
            self._awaiting_graph = self._current_graph
        elif self._superseded_errors:
            self.terminal_failure = True

    def _generation_started(self, graph: str) -> None:
        if self._awaiting_graph is not None:
            if graph != self._awaiting_graph:
                self.terminal_failure = True
                return
            self._superseded_errors.extend(self._current_errors)
            self._current_errors.clear()
            self._awaiting_graph = None
            self._current_graph = graph
            self._superseding = True
            self._evidence_index = 0
            return

        if self._current_errors or self._superseded_errors:
            self.terminal_failure = True
            return
        self._current_graph = graph
        self._superseding = False
        self._evidence_index = 0

    def _compiler_error(self, error: str) -> None:
        if self._current_graph is None:
            self._unscoped_errors.append(error)
            self.terminal_failure = True
            return
        self._current_errors.append(error)
        if self._superseding:
            self.terminal_failure = True

    def _advance_evidence(self, event: str) -> None:
        if (
            self._superseding
            and self._evidence_index < len(_SUPERSEDING_EVIDENCE)
            and event == _SUPERSEDING_EVIDENCE[self._evidence_index]
        ):
            self._evidence_index += 1


def capture_compile_baseline(
    project_root: Path,
    unity_log_file: Path,
) -> CompileBaseline | ToolResponse:
    """Capture the pre-deploy DLL identity and append-only log boundary."""
    dll_path = project_root / _DLL_RELATIVE_PATH
    dll_identity = _dll_identity(dll_path)
    try:
        # A FIFO must not wait for a writer before we can reject its file type.
        # O_NONBLOCK is POSIX-only; Windows regular-file opens use the same check.
        with open(
            unity_log_file,
            "rb",
            opener=lambda path, flags: os.open(path, flags | getattr(os, "O_NONBLOCK", 0)),
        ) as log_file:
            log_status = os.fstat(log_file.fileno())
            if not stat.S_ISREG(log_status.st_mode):
                return _config_error("Unity Editor log must be a regular file.")
            log_header = log_file.read(_LOG_HEADER_LIMIT_BYTES)
    except OSError:
        return _config_error("Unity Editor log is unavailable.")

    log_project_root = _log_project_root(log_header)
    if log_project_root is None:
        return _config_error("Unity Editor log project identity is unavailable.")
    if log_project_root != project_root.resolve():
        return _config_error("Unity Editor log belongs to a different project.")

    return CompileBaseline(
        dll_path=dll_path,
        dll_size=None if dll_identity is None else dll_identity[0],
        dll_mtime_ns=None if dll_identity is None else dll_identity[1],
        dll_sha256=None if dll_identity is None else dll_identity[2],
        log_path=unity_log_file,
        log_device=log_status.st_dev,
        log_inode=log_status.st_ino,
        log_size=log_status.st_size,
    )


def _log_project_root(header: bytes) -> Path | None:
    try:
        lines = header.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        return None

    for index, line in enumerate(lines):
        if line.strip().casefold() != "-projectpath":
            continue
        if index + 1 >= len(lines):
            return None
        raw_project_root = lines[index + 1].strip().strip('"')
        if not raw_project_root:
            return None
        return Path(to_wsl_path(raw_project_root)).resolve()
    return None


def wait_for_compile(
    baseline: CompileBaseline,
    *,
    changed_deploy: bool,
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> CompileObservation:
    """Observe a changed deployment through a stable DLL and appended log bytes only."""
    if not changed_deploy:
        return _observation(
            success=True,
            code="ACCEPTANCE_COMPILE_NOT_REQUIRED",
            identity=_baseline_dll_identity(baseline),
            compile_observation="not_required",
        )

    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    parser = _CompileLogParser()
    log_offset = baseline.log_size
    prior_identity: tuple[int, int, str] | None = None
    stable_observations = 0

    while True:
        if deadline - clock() <= 0:
            return _observation(
                success=False,
                code=(
                    "ACCEPTANCE_COMPILE_FAILED"
                    if parser.compiler_errors
                    else "ACCEPTANCE_COMPILE_TIMEOUT"
                ),
                identity=_dll_identity(baseline.dll_path),
                compiler_errors=parser.compiler_errors,
                superseded_compiler_errors=parser.superseded_compiler_errors,
            )

        while True:
            if deadline - clock() <= 0:
                return _observation(
                    success=False,
                    code=(
                        "ACCEPTANCE_COMPILE_FAILED"
                        if parser.compiler_errors
                        else "ACCEPTANCE_COMPILE_TIMEOUT"
                    ),
                    identity=_dll_identity(baseline.dll_path),
                    compiler_errors=parser.compiler_errors,
                    superseded_compiler_errors=parser.superseded_compiler_errors,
                )

            appended = _read_appended_log(baseline, offset=log_offset)
            if appended is None:
                return _observation(
                    success=False,
                    code="ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST",
                    identity=_dll_identity(baseline.dll_path),
                    compiler_errors=parser.compiler_errors,
                    superseded_compiler_errors=parser.superseded_compiler_errors,
                )

            appended_bytes, log_offset = appended
            try:
                decoded_chunk = decoder.decode(appended_bytes, final=False)
            except UnicodeDecodeError:
                return _observation(
                    success=False,
                    code="ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST",
                    identity=_dll_identity(baseline.dll_path),
                    compiler_errors=parser.compiler_errors,
                    superseded_compiler_errors=parser.superseded_compiler_errors,
                )

            parser.feed(decoded_chunk)
            if parser.log_evidence_lost:
                return _observation(
                    success=False,
                    code="ACCEPTANCE_COMPILE_LOG_CONTINUITY_LOST",
                    identity=_dll_identity(baseline.dll_path),
                    compiler_errors=parser.compiler_errors,
                    superseded_compiler_errors=parser.superseded_compiler_errors,
                )
            at_end_of_available_input = len(appended_bytes) < _LOG_APPEND_CHUNK_BYTES
            if at_end_of_available_input:
                parser.commit_available_input()
            if parser.terminal_failure:
                return _observation(
                    success=False,
                    code="ACCEPTANCE_COMPILE_FAILED",
                    identity=_dll_identity(baseline.dll_path),
                    compiler_errors=parser.compiler_errors,
                    superseded_compiler_errors=parser.superseded_compiler_errors,
                )
            if at_end_of_available_input:
                break

        identity = _dll_identity(baseline.dll_path)
        stable_dll = False
        if identity is not None and identity != _baseline_dll_identity(baseline):
            stable_observations = (
                stable_observations + 1 if identity == prior_identity else 1
            )
            prior_identity = identity
            stable_dll = stable_observations >= 2 and parser.compile_completed
        else:
            prior_identity = None
            stable_observations = 0

        remaining = deadline - clock()
        if remaining <= 0:
            return _observation(
                success=False,
                code=(
                    "ACCEPTANCE_COMPILE_FAILED"
                    if parser.compiler_errors
                    else "ACCEPTANCE_COMPILE_TIMEOUT"
                ),
                identity=identity,
                compiler_errors=parser.compiler_errors,
                superseded_compiler_errors=parser.superseded_compiler_errors,
            )
        if stable_dll:
            return _observation(
                success=True,
                code="ACCEPTANCE_COMPILE_OK",
                identity=identity,
                superseded_compiler_errors=parser.superseded_compiler_errors,
            )
        sleep(min(_POLL_INTERVAL_SECONDS, remaining))


def _dll_identity(path: Path) -> tuple[int, int, str] | None:
    try:
        first_status = path.stat()
        contents = path.read_bytes()
        second_status = path.stat()
    except OSError:
        return None

    if (
        first_status.st_size != second_status.st_size
        or first_status.st_mtime_ns != second_status.st_mtime_ns
    ):
        return None
    return (
        second_status.st_size,
        second_status.st_mtime_ns,
        hashlib.sha256(contents).hexdigest(),
    )


def _read_appended_log(
    baseline: CompileBaseline,
    *,
    offset: int,
) -> tuple[bytes, int] | None:
    try:
        # Open without waiting for a special-file peer, then validate the
        # same descriptor before any seek or read.
        with open(
            baseline.log_path,
            "rb",
            opener=lambda path, flags: os.open(
                path,
                flags | getattr(os, "O_NONBLOCK", 0),
            ),
        ) as log_file:
            status = os.fstat(log_file.fileno())
            if not _has_log_continuity(status, baseline) or status.st_size < offset:
                return None
            log_file.seek(offset)
            appended = log_file.read(_LOG_APPEND_CHUNK_BYTES)
            final_status = os.fstat(log_file.fileno())
            if (
                not _has_log_continuity(final_status, baseline)
                or final_status.st_size < offset + len(appended)
            ):
                return None
            return appended, offset + len(appended)
    except OSError:
        return None


def _has_log_continuity(
    status: os.stat_result,
    baseline: CompileBaseline,
) -> bool:
    return (
        status.st_dev == baseline.log_device
        and status.st_ino == baseline.log_inode
        and status.st_size >= baseline.log_size
    )


def _baseline_dll_identity(
    baseline: CompileBaseline,
) -> tuple[int, int, str] | None:
    if (
        baseline.dll_size is None
        or baseline.dll_mtime_ns is None
        or baseline.dll_sha256 is None
    ):
        return None
    return (
        baseline.dll_size,
        baseline.dll_mtime_ns,
        baseline.dll_sha256,
    )


def _observation(
    *,
    success: bool,
    code: str,
    identity: tuple[int, int, str] | None,
    compiler_errors: tuple[str, ...] = (),
    superseded_compiler_errors: tuple[str, ...] = (),
    compile_observation: str = "observed",
) -> CompileObservation:
    return CompileObservation(
        success=success,
        code=code,
        dll_size=None if identity is None else identity[0],
        dll_mtime_ns=None if identity is None else identity[1],
        dll_sha256=None if identity is None else identity[2],
        compiler_errors=compiler_errors,
        superseded_compiler_errors=superseded_compiler_errors,
        compile_observation=compile_observation,
    )


def _config_error(message: str) -> ToolResponse:
    return ToolResponse(
        success=False,
        severity=Severity.ERROR,
        code="ACCEPTANCE_CONFIG_ERROR",
        message=message,
    )
