"""Contract tests for ``editor_get_blend_shapes`` pagination (issue #241).

Pins the wrapper-side contract:

* Negative ``offset`` is rejected pre-bridge with
  ``BLEND_SHAPE_PAGINATION_OUT_OF_RANGE``.
* Oversized ``limit`` is rejected pre-bridge with the same code.
* Valid offset / limit forward to the bridge with the hierarchy path,
  the filter substring, and both pagination knobs intact.

#222 Mode A: assertion-shape strengthening — exception-asserting tests
value-pin every observable field of the response envelope (code,
severity, success) and the bridge call observability (assert_not_called
on the mock) so a partial regression surfaces all observable signals
in one diagnostic.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from prefab_sentinel import mcp_tools_editor_write


def _success_envelope() -> dict:
    return {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_BLEND_SHAPES_OK",
        "message": "ok",
        "data": {
            "blend_shapes": [],
            "total_entries": 0,
            "next_cursor": "",
            "executed": True,
        },
        "diagnostics": [],
    }


class EditorGetBlendShapesPaginationTests(unittest.TestCase):
    """Pagination contract on ``editor_get_blend_shapes``."""

    def test_negative_offset_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_write, "send_action",
            return_value=_success_envelope(),
        ) as send:
            response = mcp_tools_editor_write.editor_get_blend_shapes(
                hierarchy_path="/Avatar/Body",
                offset=-1,
            )
        send.assert_not_called()
        self.assertEqual(
            ("BLEND_SHAPE_PAGINATION_OUT_OF_RANGE", "error", False),
            (response["code"], response["severity"], response["success"]),
            msg=(
                "Negative offset must be rejected pre-bridge with "
                "BLEND_SHAPE_PAGINATION_OUT_OF_RANGE; the bridge must "
                "not be contacted."
            ),
        )

    def test_oversized_limit_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_write, "send_action",
            return_value=_success_envelope(),
        ) as send:
            response = mcp_tools_editor_write.editor_get_blend_shapes(
                hierarchy_path="/Avatar/Body",
                limit=mcp_tools_editor_write.BLEND_SHAPE_LIMIT_MAX + 1,
            )
        send.assert_not_called()
        self.assertEqual(
            ("BLEND_SHAPE_PAGINATION_OUT_OF_RANGE", "error", False),
            (response["code"], response["severity"], response["success"]),
            msg=(
                "Above-cap limit must be rejected pre-bridge so the "
                "limit cap never slips silently past the wrapper."
            ),
        )

    def test_zero_limit_rejected_pre_bridge(self) -> None:
        # Boundary: limit must be strictly positive (1..1000).
        with patch.object(
            mcp_tools_editor_write, "send_action",
            return_value=_success_envelope(),
        ) as send:
            response = mcp_tools_editor_write.editor_get_blend_shapes(
                hierarchy_path="/Avatar/Body",
                limit=0,
            )
        send.assert_not_called()
        self.assertEqual(
            "BLEND_SHAPE_PAGINATION_OUT_OF_RANGE", response["code"],
            msg="limit=0 must be rejected (lower-bound boundary).",
        )

    def test_valid_pagination_forwards_target_filter_and_both_knobs(self) -> None:
        with patch.object(
            mcp_tools_editor_write, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_write.editor_get_blend_shapes(
                hierarchy_path="/Avatar/Body",
                filter="vrc.v_",
                offset=20,
                limit=50,
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        # Value-pin the action label, both pagination knobs, the filter
        # substring, and the addressing target together — a regression
        # that drops any single knob surfaces alongside the others.
        self.assertEqual(
            ("get_blend_shapes", "/Avatar/Body", "vrc.v_", 20, 50),
            (
                kwargs["action"], kwargs["hierarchy_path"],
                kwargs["filter"], kwargs["offset"], kwargs["limit"],
            ),
            msg=(
                "Pagination forwarding must carry the target hierarchy "
                "path, the filter substring, and both knobs verbatim."
            ),
        )


if __name__ == "__main__":
    unittest.main()
