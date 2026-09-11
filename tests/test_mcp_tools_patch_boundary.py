"""Public boundary regressions for audited patch writers (#161)."""

from __future__ import annotations

import json
import unittest
from collections.abc import Callable
from typing import Any, NoReturn
from unittest.mock import patch

from prefab_sentinel import orchestrator_write
from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.mcp_tools_patch import register_patch_tools

_SECRET = "PRIVATE_SECRET_PATCH_ACQUIRE"


class _FakeServer:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., dict[str, Any]]] = {}

    def tool(
        self,
    ) -> Callable[
        [Callable[..., dict[str, Any]]],
        Callable[..., dict[str, Any]],
    ]:
        def register(
            function: Callable[..., dict[str, Any]],
        ) -> Callable[..., dict[str, Any]]:
            self.tools[function.__name__] = function
            return function

        return register


class _AcquisitionFailingSession:
    def get_orchestrator(self) -> object:
        raise RuntimeError(_SECRET)

    def resolve_scope(self, scope: str | None) -> str | None:
        return scope

    def invalidate_all(self) -> None:
        raise AssertionError("pre-dispatch acquisition failure must not invalidate caches")


class _ScopeFailingSession:
    def get_orchestrator(self) -> object:
        return object()

    def resolve_scope(self, scope: str | None) -> str | None:
        del scope
        raise RuntimeError("PRIVATE_SECRET_PATCH_SCOPE")

    def invalidate_all(self) -> None:
        raise AssertionError("scope failure before dispatch must not invalidate caches")


class _DispatchFailingOrchestrator:
    def _raise(self) -> NoReturn:
        raise RuntimeError("PRIVATE_SECRET_PATCH_DISPATCH")

    def set_material_property(self, **kwargs: Any) -> object:
        del kwargs
        self._raise()

    def copy_asset(self, **kwargs: Any) -> object:
        del kwargs
        self._raise()

    def rename_asset(self, **kwargs: Any) -> object:
        del kwargs
        self._raise()

    def delete_assets(self, *args: Any, **kwargs: Any) -> object:
        del args, kwargs
        self._raise()


class _DispatchFailingSession:
    def __init__(self) -> None:
        self.orchestrator = _DispatchFailingOrchestrator()
        self.invalidation_count = 0

    def get_orchestrator(self) -> _DispatchFailingOrchestrator:
        return self.orchestrator

    def resolve_scope(self, scope: str | None) -> str | None:
        return scope

    def invalidate_all(self) -> None:
        self.invalidation_count += 1


class _InvalidationFailingDispatchSession(_DispatchFailingSession):
    def invalidate_all(self) -> None:
        raise RuntimeError("PRIVATE_SECRET_PATCH_INVALIDATE")


class _MalformedResponseOrchestrator:
    def copy_asset(self, **kwargs: Any) -> object:
        del kwargs
        return {"success": True}


class _MalformedResponseSession:
    def __init__(self) -> None:
        self.orchestrator = _MalformedResponseOrchestrator()
        self.invalidation_count = 0

    def get_orchestrator(self) -> _MalformedResponseOrchestrator:
        return self.orchestrator

    def invalidate_all(self) -> None:
        self.invalidation_count += 1


class _IncompleteProjectedResponse:
    def to_dict(self) -> dict[str, Any]:
        return {"success": True, "data": {}}


class _IncompleteProjectionOrchestrator:
    def copy_asset(self, **kwargs: Any) -> object:
        del kwargs
        return _IncompleteProjectedResponse()


class _IncompleteProjectionSession:
    def __init__(self) -> None:
        self.orchestrator = _IncompleteProjectionOrchestrator()
        self.invalidation_count = 0

    def get_orchestrator(self) -> _IncompleteProjectionOrchestrator:
        return self.orchestrator

    def invalidate_all(self) -> None:
        self.invalidation_count += 1


class _RefreshFailingOrchestrator:
    def __init__(self) -> None:
        self.invalidated_caches: list[str] = []

    def set_material_property(
        self,
        *,
        target_path: str,
        property_name: str,
        value: str,
        dry_run: bool,
        change_reason: str | None,
    ) -> ToolResponse:
        return orchestrator_write.set_material_property(
            self,  # type: ignore[arg-type]
            target_path,
            property_name,
            value,
            dry_run=dry_run,
            change_reason=change_reason,
        )

    def maybe_auto_refresh(self) -> str:
        return "false"

    def invalidate_text_cache(self) -> None:
        self.invalidated_caches.append("text")

    def invalidate_guid_index(self) -> None:
        self.invalidated_caches.append("guid")

    def invalidate_before_cache(self) -> None:
        self.invalidated_caches.append("before")

    def invalidate_scope_files_cache(self) -> None:
        self.invalidated_caches.append("scope")


class _RefreshFailingSession:
    def __init__(self) -> None:
        self.orchestrator = _RefreshFailingOrchestrator()
        self.invalidation_count = 0

    def get_orchestrator(self) -> _RefreshFailingOrchestrator:
        return self.orchestrator

    def invalidate_all(self) -> None:
        self.invalidation_count += 1


class _InvalidationFailingRefreshSession(_RefreshFailingSession):
    def invalidate_all(self) -> None:
        raise RuntimeError("PRIVATE_SECRET_PATCH_INVALIDATE")


class _PostMutationAcquisitionFailingSession:
    def __init__(self) -> None:
        self.invalidation_count = 0

    def get_orchestrator(self) -> object:
        raise RuntimeError("PRIVATE_SECRET_PATCH_REFRESH_ACQUIRE")

    def invalidate_all(self) -> None:
        self.invalidation_count += 1


def _registered_tools(session: object) -> dict[str, Callable[..., dict[str, Any]]]:
    server = _FakeServer()
    register_patch_tools(server, session)  # type: ignore[arg-type]
    return server.tools


def _invoke_without_raising(
    tool: Callable[..., dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        return tool(**kwargs)
    except Exception as exc:
        return {
            "raised_type": type(exc).__name__,
            "raised_message": str(exc),
        }


class PatchWriterBoundaryTests(unittest.TestCase):
    def test_acquisition_failure_is_redacted_before_mutation_for_each_family(
        self,
    ) -> None:
        tools = _registered_tools(_AcquisitionFailingSession())
        cases = (
            (
                "set_material_property",
                {
                    "asset_path": "Assets/Material.mat",
                    "property_name": "_Color",
                    "value": "[1, 1, 1, 1]",
                },
            ),
            (
                "copy_asset",
                {
                    "source_path": "Assets/Source.prefab",
                    "dest_path": "Assets/Copy.prefab",
                },
            ),
            (
                "rename_asset",
                {
                    "asset_path": "Assets/Source.prefab",
                    "new_name": "Renamed.prefab",
                },
            ),
            (
                "delete_asset",
                {"asset_path": "Assets/Source.prefab"},
            ),
            (
                "delete_assets",
                {"asset_paths": ["Assets/Source.prefab"]},
            ),
        )

        for operation, kwargs in cases:
            with self.subTest(operation=operation):
                result = _invoke_without_raising(tools[operation], **kwargs)

                self.assertEqual(
                    (
                        False,
                        "error",
                        "PATCH_WRITER_BOUNDARY_FAILED",
                        "Patch writer failed before mutation.",
                        {
                            "operation": operation,
                            "phase": "acquisition",
                            "mutation_state": "not_started",
                            "cache_state": "unchanged",
                        },
                        [],
                    ),
                    (
                        result.get("success"),
                        result.get("severity"),
                        result.get("code"),
                        result.get("message"),
                        result.get("data"),
                        result.get("diagnostics"),
                    ),
                )
                self.assertNotIn(_SECRET, json.dumps(result, sort_keys=True))


    def test_delete_scope_failure_is_redacted_before_dispatch(self) -> None:
        tools = _registered_tools(_ScopeFailingSession())
        cases = (
            ("delete_asset", {"asset_path": "Assets/Source.prefab"}),
            ("delete_assets", {"asset_paths": ["Assets/Source.prefab"]}),
        )

        for operation, kwargs in cases:
            with self.subTest(operation=operation):
                result = _invoke_without_raising(
                    tools[operation],
                    scope="Assets/Feature",
                    **kwargs,
                )

                self.assertEqual(
                    (
                        False,
                        "error",
                        "PATCH_WRITER_BOUNDARY_FAILED",
                        {
                            "operation": operation,
                            "phase": "acquisition",
                            "mutation_state": "not_started",
                            "cache_state": "unchanged",
                        },
                    ),
                    (
                        result.get("success"),
                        result.get("severity"),
                        result.get("code"),
                        result.get("data"),
                    ),
                )
                self.assertNotIn(
                    "PRIVATE_SECRET_PATCH_SCOPE",
                    json.dumps(result, sort_keys=True),
                )


    def test_confirmed_dispatch_failure_is_unknown_for_each_family(self) -> None:
        cases = (
            (
                "set_material_property",
                {
                    "asset_path": "Assets/Material.mat",
                    "property_name": "_Color",
                    "value": "[1, 1, 1, 1]",
                    "confirm": True,
                    "change_reason": "test dispatch boundary",
                },
            ),
            (
                "copy_asset",
                {
                    "source_path": "Assets/Source.prefab",
                    "dest_path": "Assets/Copy.prefab",
                    "confirm": True,
                    "change_reason": "test dispatch boundary",
                },
            ),
            (
                "rename_asset",
                {
                    "asset_path": "Assets/Source.prefab",
                    "new_name": "Renamed.prefab",
                    "confirm": True,
                    "change_reason": "test dispatch boundary",
                },
            ),
            (
                "delete_asset",
                {
                    "asset_path": "Assets/Source.prefab",
                    "dry_run": False,
                    "confirm": True,
                    "change_reason": "test dispatch boundary",
                },
            ),
            (
                "delete_assets",
                {
                    "asset_paths": ["Assets/Source.prefab"],
                    "dry_run": False,
                    "confirm": True,
                    "change_reason": "test dispatch boundary",
                },
            ),
            (
                "revert_overrides",
                {
                    "asset_path": "Assets/Variant.prefab",
                    "target_file_id": "11400000",
                    "property_path": "m_Enabled",
                    "confirm": True,
                    "change_reason": "test dispatch boundary",
                },
            ),
        )

        for operation, kwargs in cases:
            with self.subTest(operation=operation):
                session = _DispatchFailingSession()
                tools = _registered_tools(session)
                if operation == "revert_overrides":
                    with patch(
                        "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
                        side_effect=RuntimeError("PRIVATE_SECRET_PATCH_DISPATCH"),
                    ):
                        result = _invoke_without_raising(tools[operation], **kwargs)
                else:
                    result = _invoke_without_raising(tools[operation], **kwargs)

                self.assertEqual(
                    (
                        False,
                        "error",
                        "PATCH_WRITER_BOUNDARY_FAILED",
                        "Patch writer outcome is unknown.",
                        {
                            "operation": operation,
                            "phase": "dispatch",
                            "mutation_state": "unknown",
                            "cache_state": "invalidated",
                        },
                        [
                            {
                                "severity": "warning",
                                "code": "PATCH_WRITER_REINSPECTION_REQUIRED",
                                "message": (
                                    "Mutation state is unknown; inspect affected assets "
                                    "before another write."
                                ),
                                "data": {},
                            }
                        ],
                        1,
                    ),
                    (
                        result.get("success"),
                        result.get("severity"),
                        result.get("code"),
                        result.get("message"),
                        result.get("data"),
                        result.get("diagnostics"),
                        session.invalidation_count,
                    ),
                )
                self.assertNotIn(
                    "PRIVATE_SECRET_PATCH_DISPATCH",
                    json.dumps(result, sort_keys=True),
                )


    def test_confirmed_dispatch_invalidation_failure_stays_structured(self) -> None:
        session = _InvalidationFailingDispatchSession()
        tools = _registered_tools(session)

        result = _invoke_without_raising(
            tools["copy_asset"],
            source_path="Assets/Source.prefab",
            dest_path="Assets/Copy.prefab",
            confirm=True,
            change_reason="test invalidation boundary",
        )

        self.assertEqual(
            (
                False,
                "error",
                "PATCH_WRITER_BOUNDARY_FAILED",
                {
                    "operation": "copy_asset",
                    "phase": "dispatch",
                    "mutation_state": "unknown",
                    "cache_state": "unknown",
                },
            ),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                result.get("data"),
            ),
        )
        self.assertNotIn(
            "PRIVATE_SECRET_PATCH_INVALIDATE",
            json.dumps(result, sort_keys=True),
        )


    def test_confirmed_malformed_response_is_unknown_and_invalidates_cache(
        self,
    ) -> None:
        session = _MalformedResponseSession()
        tools = _registered_tools(session)

        result = _invoke_without_raising(
            tools["copy_asset"],
            source_path="Assets/Source.prefab",
            dest_path="Assets/Copy.prefab",
            confirm=True,
            change_reason="test malformed response boundary",
        )

        self.assertEqual(
            (
                False,
                "error",
                "PATCH_WRITER_BOUNDARY_FAILED",
                {
                    "operation": "copy_asset",
                    "phase": "dispatch",
                    "mutation_state": "unknown",
                    "cache_state": "invalidated",
                },
                1,
            ),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                result.get("data"),
                session.invalidation_count,
            ),
        )


    def test_confirmed_incomplete_projection_is_unknown(self) -> None:
        session = _IncompleteProjectionSession()
        tools = _registered_tools(session)

        result = _invoke_without_raising(
            tools["copy_asset"],
            source_path="Assets/Source.prefab",
            dest_path="Assets/Copy.prefab",
            confirm=True,
            change_reason="test incomplete projection boundary",
        )

        self.assertEqual(
            (
                False,
                "error",
                "PATCH_WRITER_BOUNDARY_FAILED",
                {
                    "operation": "copy_asset",
                    "phase": "dispatch",
                    "mutation_state": "unknown",
                    "cache_state": "invalidated",
                },
                1,
            ),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                result.get("data"),
                session.invalidation_count,
            ),
        )


    def test_dry_run_dispatch_failure_is_redacted_without_invalidation(self) -> None:
        session = _DispatchFailingSession()
        tools = _registered_tools(session)

        result = _invoke_without_raising(
            tools["copy_asset"],
            source_path="Assets/Source.prefab",
            dest_path="Assets/Copy.prefab",
        )

        self.assertEqual(
            (
                False,
                "error",
                "PATCH_WRITER_BOUNDARY_FAILED",
                "Patch writer failed before mutation.",
                {
                    "operation": "copy_asset",
                    "phase": "dispatch",
                    "mutation_state": "not_started",
                    "cache_state": "unchanged",
                },
                [],
                0,
            ),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                result.get("message"),
                result.get("data"),
                result.get("diagnostics"),
                session.invalidation_count,
            ),
        )
        self.assertNotIn(
            "PRIVATE_SECRET_PATCH_DISPATCH",
            json.dumps(result, sort_keys=True),
        )


    def test_refresh_failure_preserves_applied_success_and_invalidates_caches(
        self,
    ) -> None:
        session = _RefreshFailingSession()
        tools = _registered_tools(session)
        producer_response = {
            "success": True,
            "severity": "info",
            "code": "MAT_PROP_APPLIED",
            "message": "Material property updated.",
            "data": {"read_only": False, "applied": 1},
            "diagnostics": [],
        }

        with patch.object(
            orchestrator_write,
            "_write_material_property",
            return_value=producer_response,
        ):
            result = tools["set_material_property"](
                asset_path="Assets/Material.mat",
                property_name="_Color",
                value="[1, 1, 1, 1]",
                confirm=True,
                change_reason="test refresh boundary",
            )

        self.assertEqual(
            (
                True,
                "warning",
                "MAT_PROP_APPLIED",
                "Material property updated.",
                {
                    "read_only": False,
                    "applied": 1,
                    "auto_refresh": "false",
                    "mutation_state": "applied",
                    "cache_state": "invalidated",
                },
                [
                    {
                        "severity": "warning",
                        "code": "PATCH_WRITER_REINSPECTION_REQUIRED",
                        "message": (
                            "Mutation was applied, but cache refresh failed; "
                            "inspect affected assets before another write."
                        ),
                        "data": {},
                    }
                ],
                1,
                ("text", "guid", "before", "scope"),
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                result["data"],
                result["diagnostics"],
                session.invalidation_count,
                tuple(session.orchestrator.invalidated_caches),
            ),
        )
        self.assertNotIn("auto_refresh", result)


    def test_refresh_session_invalidation_failure_stays_applied(self) -> None:
        session = _InvalidationFailingRefreshSession()
        tools = _registered_tools(session)
        producer_response = {
            "success": True,
            "severity": "info",
            "code": "MAT_PROP_APPLIED",
            "message": "Material property updated.",
            "data": {"read_only": False, "applied": 1},
            "diagnostics": [],
        }

        with patch.object(
            orchestrator_write,
            "_write_material_property",
            return_value=producer_response,
        ):
            result = _invoke_without_raising(
                tools["set_material_property"],
                asset_path="Assets/Material.mat",
                property_name="_Color",
                value="[1, 1, 1, 1]",
                confirm=True,
                change_reason="test invalidation boundary",
            )

        self.assertEqual(
            (
                True,
                "warning",
                "MAT_PROP_APPLIED",
                "applied",
                "unknown",
            ),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                (result.get("data") or {}).get("mutation_state"),
                (result.get("data") or {}).get("cache_state"),
            ),
        )
        self.assertNotIn(
            "PRIVATE_SECRET_PATCH_INVALIDATE",
            json.dumps(result, sort_keys=True),
        )


    def test_revert_refresh_failure_preserves_applied_success(self) -> None:
        session = _RefreshFailingSession()
        tools = _registered_tools(session)
        applied = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REVERT_OVERRIDES_APPLIED",
            message="Override reverted.",
            data={"read_only": False, "reverted": 1},
        )

        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
            return_value=applied,
        ):
            result = tools["revert_overrides"](
                asset_path="Assets/Variant.prefab",
                target_file_id="11400000",
                property_path="m_Enabled",
                confirm=True,
                change_reason="test refresh boundary",
            )

        self.assertEqual(
            (
                True,
                "warning",
                "REVERT_OVERRIDES_APPLIED",
                "Override reverted.",
                {
                    "read_only": False,
                    "reverted": 1,
                    "auto_refresh": "false",
                    "mutation_state": "applied",
                    "cache_state": "invalidated",
                },
                [
                    {
                        "severity": "warning",
                        "code": "PATCH_WRITER_REINSPECTION_REQUIRED",
                        "message": (
                            "Mutation was applied, but cache refresh failed; "
                            "inspect affected assets before another write."
                        ),
                        "data": {},
                    }
                ],
                1,
                None,
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                result["data"],
                result["diagnostics"],
                session.invalidation_count,
                result.get("auto_refresh"),
            ),
        )


    def test_revert_refresh_invalidation_failure_stays_applied(self) -> None:
        session = _InvalidationFailingRefreshSession()
        tools = _registered_tools(session)
        applied = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REVERT_OVERRIDES_APPLIED",
            message="Override reverted.",
            data={"read_only": False, "reverted": 1},
        )

        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
            return_value=applied,
        ):
            result = _invoke_without_raising(
                tools["revert_overrides"],
                asset_path="Assets/Variant.prefab",
                target_file_id="11400000",
                property_path="m_Enabled",
                confirm=True,
                change_reason="test invalidation boundary",
            )

        self.assertEqual(
            (
                True,
                "warning",
                "REVERT_OVERRIDES_APPLIED",
                "applied",
                "unknown",
            ),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                (result.get("data") or {}).get("mutation_state"),
                (result.get("data") or {}).get("cache_state"),
            ),
        )
        self.assertNotIn(
            "PRIVATE_SECRET_PATCH_INVALIDATE",
            json.dumps(result, sort_keys=True),
        )


    def test_revert_refresh_acquisition_failure_is_applied_not_unknown(
        self,
    ) -> None:
        session = _PostMutationAcquisitionFailingSession()
        tools = _registered_tools(session)
        applied = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REVERT_OVERRIDES_APPLIED",
            message="Override reverted.",
            data={"read_only": False, "reverted": 1},
        )

        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
            return_value=applied,
        ):
            result = _invoke_without_raising(
                tools["revert_overrides"],
                asset_path="Assets/Variant.prefab",
                target_file_id="11400000",
                property_path="m_Enabled",
                confirm=True,
                change_reason="test refresh acquisition boundary",
            )

        self.assertEqual(
            (
                True,
                "warning",
                "REVERT_OVERRIDES_APPLIED",
                {
                    "read_only": False,
                    "reverted": 1,
                    "auto_refresh": "false",
                    "mutation_state": "applied",
                    "cache_state": "invalidated",
                },
                1,
            ),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                result.get("data"),
                session.invalidation_count,
            ),
        )
        self.assertNotIn(
            "PRIVATE_SECRET_PATCH_REFRESH_ACQUIRE",
            json.dumps(result, sort_keys=True),
        )
