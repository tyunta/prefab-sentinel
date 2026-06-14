"""Contract tests for the reference-scan MCP tool's ignore-GUID surface.

Two blocks:

* Loader contract block — exercises
  :func:`prefab_sentinel.ignore_guids_io.load_ignore_guids_file` on
  real fixture files in ``tempfile.TemporaryDirectory`` (Tier 1).
* Validation tool contract block — exercises the ``validate_refs`` MCP
  tool's caller-list / file-load / union plumbing through a mocked
  orchestrator (Tier 1).

Test files patch the orchestrator / session symbols at the
``session_cache`` and ``session`` namespaces (the same boundary that
``ProjectSession`` itself activates against).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from prefab_sentinel.contracts import ToolResponse
from prefab_sentinel.ignore_guids_io import (
    IGNORE_GUIDS_RELATIVE_PATH,
    load_ignore_guids_file,
    parse_ignore_guid_text,
)
from prefab_sentinel.session import ProjectSession

# ---------------------------------------------------------------------------
# Loader contract block
# ---------------------------------------------------------------------------


class LoaderAbsentFileTests(unittest.TestCase):
    """A scope with no ``config/ignore_guids.txt`` contributes nothing."""

    def test_missing_file_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp)
            # Scope dir exists but the conventional file is not created.
            self.assertEqual([], load_ignore_guids_file(scope))

    def test_non_directory_scope_returns_empty_list(self) -> None:
        # The convention only addresses directory scopes; a non-directory
        # path (a stray file or a missing path) is treated as having no
        # baseline entries rather than raising.
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist"
            self.assertEqual([], load_ignore_guids_file(missing))


class ParseIgnoreGuidTextTests(unittest.TestCase):
    """Issue #256 — canonical text parser is the single source of truth.

    The conventional-file loader and the benchmark CLI parser are thin
    adapters; the parsing semantics (blank-line drop, ``#``-introduced
    comment strip, document order preserved) live exclusively here.
    """

    def test_comments_and_blanks_stripped_order_preserved(self) -> None:
        # Mixed input: a leading comment line, a GUID line, a blank
        # line, and a GUID with an inline trailing comment.  The parser
        # must keep the two GUIDs in source order with the inline
        # comment stripped (and the trailing whitespace before ``#``
        # discarded by the strip).
        self.assertEqual(
            ["guid1", "guid2"],
            parse_ignore_guid_text("# header\nguid1\n\nguid2 # trail\n"),
            msg=(
                "Canonical parser must drop comment lines, drop blank "
                "lines, strip inline ``#``-introduced trailers, and "
                "preserve document order."
            ),
        )

    def test_empty_input_yields_empty_list(self) -> None:
        # Empty text must yield an empty list (not raise, not return
        # None) so adapters can treat the parser as a total function.
        self.assertEqual(
            [],
            parse_ignore_guid_text(""),
            msg=(
                "Canonical parser must return [] on empty input — "
                "callers treat the parser as a total function over str."
            ),
        )

    def test_inline_comment_immediately_after_entry_stripped(self) -> None:
        # The split-on-``#``-then-strip rule must remove the trailing
        # whitespace before the marker; otherwise the entry would leak
        # ``"abc "`` into the result.
        self.assertEqual(
            ["abc"],
            parse_ignore_guid_text("abc # comment\n"),
            msg=(
                "Inline ``#`` comment must be stripped before the entry "
                "is recorded, including the trailing whitespace before "
                "the marker."
            ),
        )


class LoaderParsingTests(unittest.TestCase):
    """Comments + blank lines are stripped; document order preserved."""

    def _write_and_load(self, content: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp)
            target = scope / IGNORE_GUIDS_RELATIVE_PATH
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return load_ignore_guids_file(scope)

    def test_single_guid_no_comment(self) -> None:
        self.assertEqual(
            ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
            self._write_and_load("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"),
        )

    def test_trailing_comment_after_guid(self) -> None:
        self.assertEqual(
            ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
            self._write_and_load(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  # builtin asset\n",
            ),
        )

    def test_leading_comment_line(self) -> None:
        self.assertEqual(
            ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
            self._write_and_load(
                "# header\nbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
            ),
        )

    def test_multiple_guids_with_blank_lines(self) -> None:
        # Order must be preserved exactly; blank lines do not collapse
        # adjacent GUIDs into a single entry.
        self.assertEqual(
            [
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            ],
            self._write_and_load(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                "\n"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
            ),
        )

    def test_leading_and_trailing_whitespace_stripped(self) -> None:
        self.assertEqual(
            ["cccccccccccccccccccccccccccccccc"],
            self._write_and_load(
                "   cccccccccccccccccccccccccccccccc   \n",
            ),
        )


class LoaderUnreadableFileTests(unittest.TestCase):
    """A file that cannot be decoded as UTF-8 reads as absent."""

    def test_invalid_utf8_bytes_yield_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp)
            target = scope / IGNORE_GUIDS_RELATIVE_PATH
            target.parent.mkdir(parents=True, exist_ok=True)
            # 0xff is not a valid leading UTF-8 byte; decoding raises
            # UnicodeDecodeError which the loader maps to "absent".
            target.write_bytes(b"\xff\xfe\xfd not utf-8 \n")
            self.assertEqual([], load_ignore_guids_file(scope))


# ---------------------------------------------------------------------------
# Validation tool contract block
# ---------------------------------------------------------------------------


def _success_resp() -> ToolResponse:
    """Return a minimal success ToolResponse the orchestrator can hand back."""
    from prefab_sentinel.contracts import Severity

    return ToolResponse(
        success=True,
        severity=Severity.INFO,
        code="REF_SCAN_OK",
        message="ok",
        data={"broken_count": 0},
        diagnostics=[],
    )


class _ScopedSessionFixture:
    """Helper that builds a ProjectSession + registered tool callable.

    Each test case sets up a tmp directory that *is* the scope (so
    ``resolve_scope_path`` can return that directory), patches the
    session_cache / session symbols, and exposes the registered
    ``validate_refs`` callable to the test body.
    """

    def __init__(self, test: unittest.TestCase) -> None:
        self.test = test

    def __enter__(self) -> tuple[Path, MagicMock, Callable[..., dict[str, Any]]]:
        # Build a Unity-shaped project root: ``<root>/Assets/<scope>``.
        # ``session.activate`` requires the project root to have an
        # ``Assets/`` directory; the scope path is the directory under
        # which the ignore-GUID file's conventional ``config/`` lives.
        self._tmp = tempfile.TemporaryDirectory()
        project_root = Path(self._tmp.name)
        scope = project_root / "Assets" / "MyScope"
        scope.mkdir(parents=True)

        # Patch the orchestrator + scope/root resolution symbols.  The
        # script-name map builder is patched to a no-op so activation
        # warms its cache without scanning real files.
        self._patches = [
            patch(
                "prefab_sentinel.session_cache.build_script_name_map",
                return_value={},
            ),
            patch("prefab_sentinel.session_cache.Phase1Orchestrator"),
            patch(
                "prefab_sentinel.session.resolve_scope_path",
                return_value=scope,
            ),
            patch(
                "prefab_sentinel.session.find_project_root",
                return_value=project_root,
            ),
        ]
        for p in self._patches:
            p.start()

        # Build an orchestrator mock that records the validate_refs call.
        orch_mock = MagicMock()
        orch_mock.validate_refs.return_value = _success_resp()

        # ProjectSession.get_orchestrator() returns whatever the cache
        # build returned; we replace the cache's lazy builder so it
        # hands back ``orch_mock`` regardless of the cache's path.
        session = ProjectSession(project_root=project_root)
        session._cache.get_orchestrator = MagicMock(return_value=orch_mock)

        asyncio.run(session.activate(str(scope), project_root=str(project_root)))

        # Register the validation tools against a FastMCP-shaped stub
        # that records registrations.  We don't need a real FastMCP
        # instance here — only the registered callable, which we extract
        # from the captured registration.
        from prefab_sentinel.mcp_tools_validation import register_validation_tools

        registered: dict[str, Callable[..., dict[str, Any]]] = {}

        class _Server:
            def tool(self_inner) -> Callable[..., Any]:  # noqa: N805
                def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
                    registered[fn.__name__] = fn
                    return fn

                return deco

        register_validation_tools(_Server(), session)
        validate_refs = registered["validate_refs"]

        return scope, orch_mock, validate_refs

    def __exit__(self, exc_type, exc, tb) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()


class ValidationToolForwardingTests(unittest.TestCase):
    """The MCP tool forwards a single merged collection to the orchestrator."""

    def test_caller_supplied_list_is_forwarded_when_no_file_present(self) -> None:
        with _ScopedSessionFixture(self) as (scope, orch_mock, validate_refs):
            response = validate_refs(
                scope=str(scope),
                ignore_asset_guids=["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
            )
        # Pin: orchestrator received the caller list verbatim, and the
        # response is a success envelope (no exception text in either).
        kwargs = orch_mock.validate_refs.call_args.kwargs
        self.assertEqual(
            (
                ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",),
                True,
            ),
            (
                kwargs["ignore_asset_guids"],
                response["success"],
            ),
        )

    def test_file_is_auto_loaded_when_caller_omits_list(self) -> None:
        with _ScopedSessionFixture(self) as (scope, orch_mock, validate_refs):
            file_path = scope / IGNORE_GUIDS_RELATIVE_PATH
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "# baseline\n"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
                encoding="utf-8",
            )

            response = validate_refs(scope=str(scope))

        kwargs = orch_mock.validate_refs.call_args.kwargs
        # The orchestrator must have received exactly the two parsed
        # GUIDs (comment line excluded, no caller list contributed).
        self.assertEqual(
            (
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            ),
            kwargs["ignore_asset_guids"],
        )
        # The response must carry an informational diagnostic naming
        # the file and the contribution count.
        diags = response["diagnostics"]
        matching = [
            d for d in diags
            if d.get("code") == "IGNORE_GUIDS_FILE_LOADED"
        ]
        self.assertEqual(
            (1, str(file_path), 2),
            (
                len(matching),
                matching[0]["data"]["path"] if matching else None,
                matching[0]["data"]["count"] if matching else None,
            ),
        )

    def test_project_baseline_is_forwarded_with_scope_ignore_guids(self) -> None:
        with _ScopedSessionFixture(self) as (scope, orch_mock, validate_refs):
            project_root = scope.parents[1]
            baseline_path = project_root / "config" / "diagnostics_baseline.json"
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                '{"version": 1, "known_diagnostics": ["known-key"]}',
                encoding="utf-8",
            )
            file_path = scope / IGNORE_GUIDS_RELATIVE_PATH
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
                encoding="utf-8",
            )

            response = validate_refs(scope=str(scope))

        kwargs = orch_mock.validate_refs.call_args.kwargs
        forwarded_baseline = kwargs.get("diagnostics_baseline")
        self.assertEqual(
            (
                ("known-key",),
                str(baseline_path),
                "loaded",
                ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",),
                True,
            ),
            (
                None if forwarded_baseline is None else forwarded_baseline.known_diagnostics,
                None if forwarded_baseline is None else forwarded_baseline.path,
                None if forwarded_baseline is None else forwarded_baseline.status,
                kwargs["ignore_asset_guids"],
                any(
                    diag.get("code") == "IGNORE_GUIDS_FILE_LOADED"
                    for diag in response["diagnostics"]
                ),
            ),
        )

    def test_invalid_project_baseline_returns_error_before_orchestration(self) -> None:
        with _ScopedSessionFixture(self) as (scope, orch_mock, validate_refs):
            project_root = scope.parents[1]
            baseline_path = project_root / "config" / "diagnostics_baseline.json"
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text("{", encoding="utf-8")

            response = validate_refs(scope=str(scope))

        self.assertEqual(
            (
                "DIAGNOSTICS_BASELINE_INVALID",
                "error",
                {"path": str(baseline_path), "read_only": True},
                0,
            ),
            (
                response["code"],
                response["severity"],
                response["data"],
                orch_mock.validate_refs.call_count,
            ),
        )

    def test_caller_list_and_file_entries_are_unioned(self) -> None:
        with _ScopedSessionFixture(self) as (scope, orch_mock, validate_refs):
            file_path = scope / IGNORE_GUIDS_RELATIVE_PATH
            file_path.parent.mkdir(parents=True, exist_ok=True)
            # File contains the same GUID the caller passes plus one
            # unique entry; the merged forward must dedupe.
            file_path.write_text(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
                encoding="utf-8",
            )

            validate_refs(
                scope=str(scope),
                ignore_asset_guids=["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
            )

        kwargs = orch_mock.validate_refs.call_args.kwargs
        # Dedup: the duplicate must be counted once.  Insertion order
        # places the caller-supplied entry first (preserves traceability
        # of caller intent).
        self.assertEqual(
            (
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            ),
            kwargs["ignore_asset_guids"],
        )

    def test_invalid_guid_in_file_surfaces_through_service_envelope(self) -> None:
        # The orchestrator's existing REF001 envelope returns when the
        # merged list contains a non-32-char-hex token.  Here we wire the
        # mock orchestrator to return the canonical REF001 envelope so
        # we observe the boundary forwards the malformed entry through.
        from prefab_sentinel.contracts import Severity

        ref001 = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="REF001",
            message="ignore_asset_guids must contain only 32-character hexadecimal GUIDs.",
            data={
                "scope": "",
                "invalid_ignore_asset_guids": ["not-a-guid"],
                "read_only": True,
            },
            diagnostics=[],
        )

        with _ScopedSessionFixture(self) as (scope, orch_mock, validate_refs):
            orch_mock.validate_refs.return_value = ref001

            file_path = scope / IGNORE_GUIDS_RELATIVE_PATH
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("not-a-guid\n", encoding="utf-8")

            response = validate_refs(scope=str(scope))

        self.assertEqual(
            ("REF001", "error", ["not-a-guid"]),
            (
                response["code"],
                response["severity"],
                response["data"]["invalid_ignore_asset_guids"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
