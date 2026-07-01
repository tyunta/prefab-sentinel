from __future__ import annotations

import unittest
from unittest.mock import patch

from prefab_sentinel import mcp_tools_editor_geometry


def _success_envelope() -> dict:
    return {
        "success": True,
        "severity": "info",
        "code": "OK",
        "message": "ok",
        "data": {},
        "diagnostics": [],
    }


class EditorGeometryWrapperTests(unittest.TestCase):
    def test_editor_get_transform_returns_live_transform_payload(self) -> None:
        bridge_response = _success_envelope()
        bridge_response["data"] = {
            "hierarchy_path": "/World/Button",
            "world_position": [1.0, 2.0, 3.0],
            "local_position": [0.0, 1.0, 0.0],
        }
        with patch.object(
            mcp_tools_editor_geometry, "send_action", return_value=bridge_response,
        ) as send:
            response = mcp_tools_editor_geometry.editor_get_transform("/World/Button")

        self.assertEqual(
            bridge_response,
            response,
            msg=f"transform wrapper must return bridge payload unchanged: {response!r}",
        )
        send.assert_called_once_with(
            action="get_transform",
            hierarchy_path="/World/Button",
        )

    def test_editor_geometry_wrappers_forward_selectors(self) -> None:
        with patch.object(
            mcp_tools_editor_geometry, "send_action", return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_geometry.editor_get_bounds(
                "/World/Button",
                source="rect_transform",
                include_children=False,
            )
            mcp_tools_editor_geometry.editor_measure_distance(
                "/World/Chair",
                "/World/Button",
                mode="surface",
                bounds_source="rect_transform",
            )

        self.assertEqual(
            [
                {
                    "action": "get_bounds",
                    "hierarchy_path": "/World/Button",
                    "bounds_source": "rect_transform",
                    "include_children": False,
                },
                {
                    "action": "measure_distance",
                    "hierarchy_path": "/World/Chair",
                    "target_path": "/World/Button",
                    "distance_mode": "surface",
                    "bounds_source": "rect_transform",
                },
            ],
            [call.kwargs for call in send.call_args_list],
            msg=f"geometry wrapper forwarding mismatch: {send.call_args_list!r}",
        )
