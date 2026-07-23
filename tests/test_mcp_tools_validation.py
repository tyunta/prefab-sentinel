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
import json
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
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


def _material_success_resp() -> ToolResponse:
    from prefab_sentinel.contracts import Severity

    return ToolResponse(
        success=True,
        severity=Severity.INFO,
        code="MATERIAL_VALIDATION_OK",
        message="Material validation completed.",
        data={"summary": {"scanned_files": 1}},
        diagnostics=[],
    )



def _baseline_update_source_resp(
    *,
    success: bool = True,
    code: str = "SOURCE_OK",
    new: tuple[str, ...] = ("source/new",),
    resolved: tuple[str, ...] = (),
    include_classification: bool = True,
) -> ToolResponse:
    from prefab_sentinel.contracts import Severity

    data: dict[str, Any] = {}
    if include_classification:
        data["diagnostics_baseline"] = {
            "status": "loaded",
            "path": "/project/config/diagnostics_baseline.json",
            "new_count": len(new),
            "known_count": 0,
            "resolved_count": len(resolved),
            "new": [{"key": key} for key in new],
            "known": [],
            "resolved": [{"key": key} for key in resolved],
        }
    return ToolResponse(
        success=success,
        severity=Severity.INFO if success else Severity.ERROR,
        code=code,
        message="source completed" if success else "source failed",
        data=data,
        diagnostics=[],
    )


def _registered_validation_tool(
    session: ProjectSession,
    name: str,
) -> Callable[..., dict[str, Any]]:
    from prefab_sentinel.mcp_tools_validation import register_validation_tools
    from tests._mcp_tool_recorder import record_tools

    return record_tools(register_validation_tools, session).get(name)


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
        cache = cast(Any, session._cache)
        cache.get_orchestrator = MagicMock(return_value=orch_mock)

        asyncio.run(session.activate(str(scope), project_root=str(project_root)))

        from prefab_sentinel.mcp_tools_validation import register_validation_tools
        from tests._mcp_tool_recorder import record_tools

        validate_refs: Callable[..., dict[str, Any]] = record_tools(
            register_validation_tools, session
        ).get("validate_refs")

        return scope, orch_mock, validate_refs

    def __exit__(self, exc_type, exc, tb) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()


class ValidationRecorderSourceInvariantTests(unittest.TestCase):
    def test_validation_registration_uses_shared_tool_recorder(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        local_server_stub = "class " + "_Server"
        local_cast_stub = "cast" + "(Any, " + "_Server())"

        self.assertNotIn(
            local_server_stub,
            source,
            msg="Validation registration tests must not define local _Server recorder stubs.",
        )
        self.assertNotIn(
            local_cast_stub,
            source,
            msg="Validation registration tests must delegate registration to record_tools.",
        )


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


class ValidateMaterialsToolForwardingTests(unittest.TestCase):
    def test_omitted_scope_without_session_scope_returns_error_before_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            orch_mock = MagicMock()
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            validate_materials = _registered_validation_tool(session, "validate_materials")

            response = validate_materials()

        self.assertEqual(
            (False, "error", "MATERIAL_VALIDATION_SCOPE_REQUIRED", 0),
            (
                response["success"],
                response["severity"],
                response["code"],
                orch_mock.validate_materials.call_count,
            ),
        )

    def test_blank_scope_without_session_scope_returns_error_before_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            orch_mock = MagicMock()
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            validate_materials = _registered_validation_tool(session, "validate_materials")

            for blank_scope in ("", "   "):
                with self.subTest(scope=repr(blank_scope)):
                    orch_mock.reset_mock()

                    response = validate_materials(scope=blank_scope)

                    self.assertEqual(
                        (False, "error", "MATERIAL_VALIDATION_SCOPE_REQUIRED", 0),
                        (
                            response["success"],
                            response["severity"],
                            response["code"],
                            orch_mock.validate_materials.call_count,
                        ),
                    )

    def test_explicit_scope_and_details_are_forwarded_to_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            orch_mock = MagicMock()
            orch_mock.validate_materials.return_value = _material_success_resp()
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            validate_materials = _registered_validation_tool(session, "validate_materials")

            response = validate_materials(scope="Assets/UI", include_details=True)

        kwargs = orch_mock.validate_materials.call_args.kwargs
        self.assertEqual(
            ("Assets/UI", True, "MATERIAL_VALIDATION_OK", {"summary": {"scanned_files": 1}}),
            (
                kwargs["scope"],
                kwargs["include_details"],
                response["code"],
                response["data"],
            ),
        )

    def test_timeout_is_forwarded_to_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            orch_mock = MagicMock()
            orch_mock.validate_materials.return_value = _material_success_resp()
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            validate_materials = _registered_validation_tool(session, "validate_materials")

            try:
                response = validate_materials(
                    scope="Assets/UI",
                    include_details=True,
                    timeout_sec=0.25,
                )
            except TypeError as exc:
                self.fail(
                    "Expected validate_materials timeout_sec forwarding, "
                    f"observed unsupported signature: {exc}."
                )

        kwargs = orch_mock.validate_materials.call_args.kwargs
        self.assertEqual(
            ("Assets/UI", True, 0.25, "MATERIAL_VALIDATION_OK"),
            (
                kwargs["scope"],
                kwargs["include_details"],
                kwargs["timeout_sec"],
                response["code"],
            ),
        )

    def test_activated_session_scope_is_used_when_explicit_scope_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            orch_mock = MagicMock()
            orch_mock.validate_materials.return_value = _material_success_resp()
            session = ProjectSession(project_root=project_root)
            session._scope = Path("Assets/Scoped")
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            validate_materials = _registered_validation_tool(session, "validate_materials")

            response = validate_materials()

        kwargs = orch_mock.validate_materials.call_args.kwargs
        self.assertEqual(
            ("Assets/Scoped", False, True),
            (
                kwargs["scope"],
                kwargs["include_details"],
                response["success"],
            ),
        )


class InspectMaterialAssetToolForwardingTests(unittest.TestCase):
    def test_mode_and_property_names_are_forwarded_to_orchestrator(self) -> None:
        from prefab_sentinel.contracts import Severity

        expected = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="INSPECT_MATERIAL_ASSET_RESULT",
            message="inspect.material_asset completed.",
            data={"mode": "summary", "selected_properties": {"_Color": {}}},
            diagnostics=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            orch_mock = MagicMock()
            orch_mock.inspect_material_asset.return_value = expected
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            inspect_material_asset = _registered_validation_tool(
                session,
                "inspect_material_asset",
            )

            try:
                response = inspect_material_asset(
                    asset_path="Assets/UI/Button.mat",
                    mode="summary",
                    property_names=["_Color"],
                )
            except TypeError as exc:
                self.fail(
                    "Expected inspect_material_asset mode/property_names forwarding, "
                    f"observed unsupported signature: {exc}."
                )

        kwargs = orch_mock.inspect_material_asset.call_args.kwargs
        self.assertEqual(
            (
                "Assets/UI/Button.mat",
                "summary",
                ["_Color"],
                "INSPECT_MATERIAL_ASSET_RESULT",
            ),
            (
                kwargs["target_path"],
                kwargs["mode"],
                kwargs["property_names"],
                response["code"],
            ),
        )


class ValidationDiagnosticsBaselineForwardingTests(unittest.TestCase):
    def test_new_validation_surfaces_load_and_forward_project_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            baseline_path = project_root / "config" / "diagnostics_baseline.json"
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                '{"version": 1, "known_diagnostics": ["known-key"]}',
                encoding="utf-8",
            )
            orch_mock = MagicMock()
            orch_mock.validate_materials.return_value = _material_success_resp()
            orch_mock.inspect_structure.return_value = _success_resp()
            orch_mock.validate_all_wiring.return_value = _success_resp()
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)

            validate_materials = _registered_validation_tool(session, "validate_materials")
            validate_structure = _registered_validation_tool(session, "validate_structure")
            validate_all_wiring = _registered_validation_tool(session, "validate_all_wiring")

            validate_materials(scope="Assets", include_details=True)
            validate_structure(asset_path="Assets/Broken.prefab")
            validate_all_wiring(asset_path="Assets/Broken.prefab")

        forwarded = (
            orch_mock.validate_materials.call_args.kwargs.get("diagnostics_baseline"),
            orch_mock.inspect_structure.call_args.kwargs.get("diagnostics_baseline"),
            orch_mock.validate_all_wiring.call_args.kwargs.get("diagnostics_baseline"),
        )
        self.assertEqual(
            (("known-key",), ("known-key",), ("known-key",)),
            tuple(None if item is None else item.known_diagnostics for item in forwarded),
        )
        self.assertEqual(
            (True, "Assets/Broken.prefab", "Assets/Broken.prefab"),
            (
                orch_mock.validate_materials.call_args.kwargs["include_details"],
                orch_mock.inspect_structure.call_args.kwargs["target_path"],
                orch_mock.validate_all_wiring.call_args.kwargs["target_path"],
            ),
        )

    def test_invalid_project_baseline_fails_before_new_validation_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            baseline_path = project_root / "config" / "diagnostics_baseline.json"
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text("{", encoding="utf-8")
            orch_mock = MagicMock()
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            calls = (
                (
                    _registered_validation_tool(session, "validate_materials"),
                    {"scope": "Assets"},
                    orch_mock.validate_materials,
                ),
                (
                    _registered_validation_tool(session, "validate_structure"),
                    {"asset_path": "Assets/Broken.prefab"},
                    orch_mock.inspect_structure,
                ),
                (
                    _registered_validation_tool(session, "validate_all_wiring"),
                    {"asset_path": "Assets/Broken.prefab"},
                    orch_mock.validate_all_wiring,
                ),
            )

            observed = []
            for tool, kwargs, orchestrator_method in calls:
                response = tool(**kwargs)
                observed.append(
                    (
                        response["code"],
                        response["severity"],
                        response["data"],
                        orchestrator_method.call_count,
                    )
                )

        self.assertEqual(
            [
                (
                    "DIAGNOSTICS_BASELINE_INVALID",
                    "error",
                    {"path": str(baseline_path), "read_only": True},
                    0,
                ),
                (
                    "DIAGNOSTICS_BASELINE_INVALID",
                    "error",
                    {"path": str(baseline_path), "read_only": True},
                    0,
                ),
                (
                    "DIAGNOSTICS_BASELINE_INVALID",
                    "error",
                    {"path": str(baseline_path), "read_only": True},
                    0,
                ),
            ],
            observed,
        )


class UpdateDiagnosticsBaselineToolTests(unittest.TestCase):
    def test_preview_dispatches_supported_sources_with_target_and_detail_knobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            orch_mock = MagicMock()
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            tool = _registered_validation_tool(session, "update_diagnostics_baseline")
            cases = (
                (
                    "validate_refs",
                    orch_mock.validate_refs,
                    {"scope": "Assets/Target.prefab", "details": True},
                ),
                (
                    "inspect_wiring",
                    orch_mock.inspect_wiring,
                    {"target_path": "Assets/Target.prefab"},
                ),
                (
                    "validate_all_wiring",
                    orch_mock.validate_all_wiring,
                    {"target_path": "Assets/Target.prefab"},
                ),
                (
                    "validate_structure",
                    orch_mock.inspect_structure,
                    {"target_path": "Assets/Target.prefab"},
                ),
                (
                    "validate_materials",
                    orch_mock.validate_materials,
                    {"scope": "Assets/Target.prefab", "include_details": True},
                ),
            )

            observed = []
            for source, method, expected_kwargs in cases:
                method.reset_mock()
                method.return_value = _baseline_update_source_resp(new=(f"{source}/new",))
                response = tool(
                    source=source,
                    target="Assets/Target.prefab",
                    details=True,
                    include_details=True,
                )
                call_kwargs = method.call_args.kwargs
                observed.append(
                    (
                        source,
                        response["code"],
                        response["data"]["added_count"],
                        response["data"]["written"],
                        tuple(
                            None if key == "diagnostics_baseline"
                            else call_kwargs.get(key)
                            for key in expected_kwargs
                        ),
                        call_kwargs["diagnostics_baseline"].known_diagnostics,
                    )
                )

        self.assertEqual(
            [
                (
                    "validate_refs",
                    "DIAGNOSTICS_BASELINE_UPDATE_PREVIEW",
                    1,
                    False,
                    ("Assets/Target.prefab", True),
                    (),
                ),
                (
                    "inspect_wiring",
                    "DIAGNOSTICS_BASELINE_UPDATE_PREVIEW",
                    1,
                    False,
                    ("Assets/Target.prefab",),
                    (),
                ),
                (
                    "validate_all_wiring",
                    "DIAGNOSTICS_BASELINE_UPDATE_PREVIEW",
                    1,
                    False,
                    ("Assets/Target.prefab",),
                    (),
                ),
                (
                    "validate_structure",
                    "DIAGNOSTICS_BASELINE_UPDATE_PREVIEW",
                    1,
                    False,
                    ("Assets/Target.prefab",),
                    (),
                ),
                (
                    "validate_materials",
                    "DIAGNOSTICS_BASELINE_UPDATE_PREVIEW",
                    1,
                    False,
                    ("Assets/Target.prefab", True),
                    (),
                ),
            ],
            observed,
        )

    def test_validate_refs_source_replay_uses_scope_ignore_guid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            scope = project_root / "Assets" / "ReplayScope"
            ignore_file = scope / IGNORE_GUIDS_RELATIVE_PATH
            ignore_file.parent.mkdir(parents=True)
            ignore_file.write_text(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
                encoding="utf-8",
            )
            orch_mock = MagicMock()
            orch_mock.validate_refs.return_value = _baseline_update_source_resp(new=("scope/new",))
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            tool = _registered_validation_tool(session, "update_diagnostics_baseline")

            response = tool(
                source="validate_refs",
                target=str(scope),
                details=True,
            )

        kwargs = orch_mock.validate_refs.call_args.kwargs
        self.assertEqual(
            (
                "DIAGNOSTICS_BASELINE_UPDATE_PREVIEW",
                ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
                str(scope),
                True,
                1,
            ),
            (
                response["code"],
                tuple(kwargs["ignore_asset_guids"]),
                kwargs["scope"],
                kwargs["details"],
                response["data"]["added_count"],
            ),
        )

    def test_write_creates_sorted_unique_json_only_when_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            baseline_path = project_root / "config" / "diagnostics_baseline.json"
            orch_mock = MagicMock()
            orch_mock.validate_refs.return_value = _baseline_update_source_resp(
                new=("zeta", "alpha", "alpha")
            )
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            tool = _registered_validation_tool(session, "update_diagnostics_baseline")

            response = tool(
                source="validate_refs",
                target="Assets",
                mode="write",
                confirm=True,
                change_reason="accept diagnostics baseline",
            )
            content = baseline_path.read_text(encoding="utf-8")

        self.assertEqual(
            (
                True,
                "DIAGNOSTICS_BASELINE_UPDATE_WRITTEN",
                True,
                True,
                2,
                ["alpha", "zeta"],
            ),
            (
                response["success"],
                response["code"],
                response["data"]["written"],
                response["data"]["would_create"],
                response["data"]["added_count"],
                response["data"]["known_diagnostics"],
            ),
        )
        self.assertEqual(
            json.dumps(
                {"version": 1, "known_diagnostics": ["alpha", "zeta"]},
                indent=2,
                sort_keys=True,
            ) + "\n",
            content,
        )


    def test_write_failure_returns_structured_error(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            baseline_path = project_root / "config" / "diagnostics_baseline.json"
            orch_mock = MagicMock()
            orch_mock.validate_refs.return_value = _baseline_update_source_resp(new=("alpha",))
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            tool = _registered_validation_tool(session, "update_diagnostics_baseline")

            with patch.object(os, "replace", side_effect=OSError("boom")):
                response = tool(
                    source="validate_refs",
                    target="Assets",
                    mode="write",
                    confirm=True,
                    change_reason="accept diagnostics baseline",
                )

        self.assertEqual(
            (
                False,
                "DIAGNOSTICS_BASELINE_WRITE_FAILED",
                "error",
                {"path": str(baseline_path), "read_only": False},
            ),
            (
                response["success"],
                response["code"],
                response["severity"],
                response["data"],
            ),
        )

    def test_write_rejects_symlinked_project_baseline_before_source_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            outside = project_root / "outside.json"
            outside.write_text("outside", encoding="utf-8")
            baseline_path = project_root / "config" / "diagnostics_baseline.json"
            baseline_path.parent.mkdir()
            baseline_path.symlink_to(outside)
            orch_mock = MagicMock()
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            tool = _registered_validation_tool(session, "update_diagnostics_baseline")

            response = tool(
                source="validate_refs",
                target="Assets",
                mode="write",
                confirm=True,
                change_reason="accept diagnostics baseline",
            )
            outside_content = outside.read_text(encoding="utf-8")

        self.assertEqual(
            (
                "DIAGNOSTICS_BASELINE_INVALID",
                "error",
                {"path": str(baseline_path), "read_only": True},
                0,
                "outside",
            ),
            (
                response["code"],
                response["severity"],
                response["data"],
                orch_mock.validate_refs.call_count,
                outside_content,
            ),
        )

    def test_write_rejects_broken_config_symlink_before_source_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "Assets").mkdir()
            config_path = project_root / "config"
            baseline_path = config_path / "diagnostics_baseline.json"
            config_path.symlink_to(
                project_root / "missing-config-target",
                target_is_directory=True,
            )
            orch_mock = MagicMock()
            session = ProjectSession(project_root=project_root)
            cache = cast(Any, session._cache)
            cache.get_orchestrator = MagicMock(return_value=orch_mock)
            tool = _registered_validation_tool(session, "update_diagnostics_baseline")

            response = tool(
                source="validate_refs",
                target="Assets",
                mode="write",
                confirm=True,
                change_reason="accept diagnostics baseline",
            )

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

    def test_source_failure_and_missing_classification_do_not_write(self) -> None:
        cases = (
            (
                _baseline_update_source_resp(success=False, code="SOURCE_FAILED"),
                "DIAGNOSTICS_BASELINE_SOURCE_FAILED",
            ),
            (
                _baseline_update_source_resp(include_classification=False),
                "DIAGNOSTICS_BASELINE_SOURCE_MISSING_CLASSIFICATION",
            ),
        )
        observed = []
        for source_response, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp:
                    project_root = Path(tmp)
                    (project_root / "Assets").mkdir()
                    baseline_path = project_root / "config" / "diagnostics_baseline.json"
                    orch_mock = MagicMock()
                    orch_mock.validate_refs.return_value = source_response
                    session = ProjectSession(project_root=project_root)
                    cache = cast(Any, session._cache)
                    cache.get_orchestrator = MagicMock(return_value=orch_mock)
                    tool = _registered_validation_tool(session, "update_diagnostics_baseline")

                    response = tool(
                        source="validate_refs",
                        target="Assets",
                        mode="write",
                        confirm=True,
                        change_reason="attempt baseline update",
                    )
                    observed.append(
                        (
                            response["code"],
                            response["severity"],
                            baseline_path.exists(),
                            response["data"].get("source_response", {}).get("code"),
                        )
                    )

        self.assertEqual(
            [
                (
                    "DIAGNOSTICS_BASELINE_SOURCE_FAILED",
                    "error",
                    False,
                    "SOURCE_FAILED",
                ),
                (
                    "DIAGNOSTICS_BASELINE_SOURCE_MISSING_CLASSIFICATION",
                    "error",
                    False,
                    None,
                ),
            ],
            observed,
        )

    def test_preconditions_fail_before_source_or_write(self) -> None:
        cases = (
            (
                ProjectSession(),
                {"source": "validate_refs", "target": "Assets"},
                "DIAGNOSTICS_BASELINE_PROJECT_ROOT_REQUIRED",
            ),
            (
                None,
                {"source": "report_json", "target": "Assets"},
                "DIAGNOSTICS_BASELINE_SOURCE_INVALID",
            ),
            (
                None,
                {"source": "validate_refs", "target": "Assets", "mode": "replace"},
                "DIAGNOSTICS_BASELINE_MODE_INVALID",
            ),
            (
                None,
                {"source": "validate_refs", "target": "Assets", "mode": "write"},
                "CHANGE_REASON_REQUIRED",
            ),
            (
                None,
                {
                    "source": "validate_refs",
                    "target": "Assets",
                    "mode": "write",
                    "confirm": True,
                    "change_reason": "",
                },
                "CHANGE_REASON_REQUIRED",
            ),
        )
        observed = []
        for supplied_session, kwargs, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp:
                    project_root = Path(tmp)
                    (project_root / "Assets").mkdir()
                    session = supplied_session or ProjectSession(project_root=project_root)
                    orch_mock = MagicMock()
                    cache = cast(Any, session._cache)
                    cache.get_orchestrator = MagicMock(return_value=orch_mock)
                    tool = _registered_validation_tool(session, "update_diagnostics_baseline")

                    response = tool(**kwargs)
                    observed.append(
                        (
                            response["code"],
                            response["severity"],
                            orch_mock.validate_refs.call_count,
                            (project_root / "config").exists(),
                        )
                    )

        self.assertEqual(
            [
                ("DIAGNOSTICS_BASELINE_PROJECT_ROOT_REQUIRED", "error", 0, False),
                ("DIAGNOSTICS_BASELINE_SOURCE_INVALID", "error", 0, False),
                ("DIAGNOSTICS_BASELINE_MODE_INVALID", "error", 0, False),
                ("CHANGE_REASON_REQUIRED", "error", 0, False),
                ("CHANGE_REASON_REQUIRED", "error", 0, False),
            ],
            observed,
        )


if __name__ == "__main__":
    unittest.main()
