"""Contract tests for the ``editor_console`` MCP tool's phase-filter argument.

Pins:

* The selector reaches the bridge action call verbatim.
* The default is ``all`` when the caller omits the selector.
* Pre-bridge size validation continues to fire before the bridge is
  contacted, regardless of the phase-filter value.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from prefab_sentinel import mcp_tools_editor_view


def _success_envelope() -> dict:
    """Return the bridge-shaped success envelope the mock returns."""
    return {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_CONSOLE_OK",
        "message": "captured 0 entries",
        "data": {"entries": [], "total_entries": 0},
        "diagnostics": [],
    }


# Shared success envelope for the ``capture_screenshot`` bridge action.
# Two screenshot test classes mock ``send_action`` against the same
# success-envelope shape; consolidating the literal here keeps the
# wrapper-side contract checked against a single source of truth so a
# bridge envelope shape change cannot drift between the two classes.
_SCREENSHOT_BRIDGE_OK = {
    "success": True,
    "severity": "info",
    "code": "EDITOR_CTRL_SCREENSHOT_OK",
    "message": "ok",
    "data": {"executed": True},
    "diagnostics": [],
}


class ConsoleCapturePhaseFilterForwardingTests(unittest.TestCase):
    """Phase-filter argument plumbing through ``editor_console``."""

    def test_phase_filter_argument_reaches_the_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_view.editor_console(phase_filter="play")

        # The bridge action is invoked exactly once with phase_filter
        # forwarded verbatim and the canonical action name.
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_console_logs", "play"),
            (kwargs["action"], kwargs["phase_filter"]),
            msg=(
                "phase_filter='play' must reach the bridge unchanged on the "
                "capture_console_logs action; neither the action name nor "
                "the filter value may be silently rewritten."
            ),
        )

    def test_default_phase_filter_is_all_when_omitted(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_view.editor_console()

        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            "all", kwargs["phase_filter"],
            msg=(
                "phase_filter default must be 'all' when the caller omits "
                "the argument; the wrapper must not drop the field."
            ),
        )

    def test_zero_size_blocked_before_bridge_even_with_phase_filter(self) -> None:
        # Pre-bridge size validation (max_entries < 1) must fire before
        # the bridge is contacted regardless of the phase-filter value;
        # the new parameter cannot be a bypass route.
        with patch.object(
            mcp_tools_editor_view, "send_action",
            return_value=_success_envelope(),
        ) as send:
            response = mcp_tools_editor_view.editor_console(
                max_entries=0,
                phase_filter="play",
            )

        send.assert_not_called()
        self.assertEqual(
            ("MAX_ENTRIES_OUT_OF_RANGE", "error", False),
            (response["code"], response["severity"], response["success"]),
            msg=(
                "max_entries=0 must short-circuit pre-bridge even when "
                "phase_filter is non-default; the new parameter cannot "
                "bypass the size-range gate."
            ),
        )


class EditorScreenshotRegionForwardingTests(unittest.TestCase):
    """Issue #249 — ``editor_screenshot`` region argument forwarding.

    Pins the wrapper-side contract for ``crop_roi``:

    * Empty value produces a bridge call whose kwargs carry no
      region field (no silent default flip).
    * Recognised preset name reaches the bridge verbatim.
    * Pixel quadruple ``"x,y,w,h"`` reaches the bridge verbatim.
    * Unrecognised value is rejected pre-bridge with
      ``CROP_ROI_INVALID``; the bridge mock is not called.
    """

    _BRIDGE_OK = _SCREENSHOT_BRIDGE_OK

    def test_empty_region_produces_no_region_field_on_kwargs(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(refresh=False)
        # send_action is called exactly once; the call carries the
        # screenshot action and no ``crop_roi`` key.  Asserting on both
        # as one tuple value-pin so a regression that silently injects
        # a region arg surfaces alongside the action name.
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_screenshot", False),
            (kwargs["action"], "crop_roi" in kwargs),
            msg=(
                "Empty crop_roi must produce no region field on the "
                "bridge call kwargs (no silent default flip)."
            ),
        )

    def test_recognised_preset_reaches_bridge_verbatim(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(
                refresh=False, crop_roi="auto_face",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_screenshot", "auto_face"),
            (kwargs["action"], kwargs["crop_roi"]),
            msg="Preset name must reach the bridge unchanged.",
        )

    def test_pixel_quadruple_reaches_bridge_verbatim(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(
                refresh=False, crop_roi="10,20,300,200",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_screenshot", "10,20,300,200"),
            (kwargs["action"], kwargs["crop_roi"]),
            msg="Pixel quadruple must reach the bridge unchanged.",
        )

    def test_unrecognised_region_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                refresh=False, crop_roi="nostrils",
            )
        # The bridge mock must not be called; the response must name
        # both the supplied value and the accepted preset set so a
        # caller can fix the request without external docs.
        send.assert_not_called()
        observed = (
            response["success"],
            response["severity"],
            response["code"],
            "nostrils" in response["message"],
            "auto_face" in response["message"],
        )
        self.assertEqual(
            (False, "error", "CROP_ROI_INVALID", True, True),
            observed,
            msg=(
                "Unrecognised crop_roi must yield CROP_ROI_INVALID and "
                "the message must name both the supplied value and "
                "the accepted preset set."
            ),
        )


class EditorScreenshotViewAllowlistTests(unittest.TestCase):
    """Issue #259 — ``editor_screenshot`` rejects view selectors outside
    the published allowlist before any bridge transport activity,
    including the optional pre-screenshot refresh.

    The allowlist is the two lower-case ASCII selectors ``"scene"`` and
    ``"game"``; every other input (path-traversal, empty, NUL byte,
    mixed case, arbitrary tokens) is rejected pre-bridge with the
    canonical ``SCREENSHOT_VIEW_INVALID`` envelope.
    """

    _BRIDGE_OK = _SCREENSHOT_BRIDGE_OK

    def test_allowlist_constant_pins_exact_selector_set_in_order(self) -> None:
        # Pin the exported allowlist tuple verbatim.  Drift (added
        # mixed-case variant, single-selector regression, ordering
        # inversion) is the failure mode this row catches.
        self.assertEqual(
            ("scene", "game"),
            mcp_tools_editor_view.SCREENSHOT_VIEW_ALLOWLIST,
            msg=(
                "Wrapper-side view allowlist must equal the two "
                "lower-case ASCII selectors ('scene', 'game') in "
                "that order (#259)."
            ),
        )

    def test_accepted_view_scene_reaches_bridge_verbatim(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(view="scene", refresh=False)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_screenshot", "scene"),
            (kwargs["action"], kwargs["view"]),
            msg=(
                "Accepted view='scene' must reach the bridge unchanged "
                "on the capture_screenshot action."
            ),
        )

    def test_accepted_view_game_reaches_bridge_verbatim(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(view="game", refresh=False)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            "game", kwargs["view"],
            msg=(
                "Accepted view='game' must reach the bridge unchanged; "
                "the allowlist must not degenerate to a single selector."
            ),
        )

    def test_path_traversal_view_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                view="../../../etc/passwd", refresh=False,
            )
        send.assert_not_called()
        self.assertEqual(
            (False, "error", "SCREENSHOT_VIEW_INVALID"),
            (response["success"], response["severity"], response["code"]),
            msg=(
                "Path-traversal payload must be rejected with "
                "SCREENSHOT_VIEW_INVALID before any bridge transport "
                "activity (#259)."
            ),
        )

    def test_empty_view_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                view="", refresh=False,
            )
        send.assert_not_called()
        self.assertEqual(
            "SCREENSHOT_VIEW_INVALID", response["code"],
            msg=(
                "Empty view selector must be rejected pre-bridge "
                "rather than composing a leading-underscore filename "
                "(#259)."
            ),
        )

    def test_nul_byte_view_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                view="scene\x00", refresh=False,
            )
        send.assert_not_called()
        self.assertEqual(
            "SCREENSHOT_VIEW_INVALID", response["code"],
            msg=(
                "NUL-byte view selector must be rejected pre-bridge "
                "rather than slipping through to the filesystem layer "
                "(#259)."
            ),
        )

    def test_reject_envelope_message_names_supplied_value_and_allowlist(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ):
            response = mcp_tools_editor_view.editor_screenshot(
                view="bogus", refresh=False,
            )
        message = response["message"]
        # Pin all three names so callers can correct the request from
        # the envelope alone, without consulting external docs.
        self.assertEqual(
            (True, True, True),
            ("bogus" in message, "scene" in message, "game" in message),
            msg=(
                "Reject envelope message must name the supplied value "
                "and both accepted selectors; observed message="
                f"{message!r}"
            ),
        )

    def test_rejected_view_suppresses_refresh_round_trip(self) -> None:
        # Refresh round-trip must not run for a rejected view — the
        # wrapper performs no transport activity at all on rejection.
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                view="bad", refresh=True,
            )
        send.assert_not_called()
        self.assertEqual(
            "SCREENSHOT_VIEW_INVALID", response["code"],
            msg=(
                "Rejected view must suppress the pre-screenshot refresh "
                "round-trip; the wrapper must perform no transport "
                "activity at all on rejection (#259)."
            ),
        )


class EditorForceSceneViewRefreshTests(unittest.TestCase):
    """Issue #242 — ``editor_force_scene_view_refresh`` routing."""

    _BRIDGE_OK = {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_FORCE_REFRESH_OK",
        "message": "ok",
        "data": {"renderers_touched": 7, "executed": True},
        "diagnostics": [],
    }

    def test_refresh_wrapper_routes_to_dedicated_action_with_no_other_kwargs(
        self,
    ) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_force_scene_view_refresh()
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        # The call must carry only the action label; any other field
        # would imply the bridge is being asked to disambiguate beyond
        # the single-round-trip contract.
        self.assertEqual(
            ("force_scene_view_refresh", 1),
            (kwargs["action"], len(kwargs)),
            msg=(
                "Force-refresh wrapper must reach exactly the "
                "force_scene_view_refresh action with no other kwargs."
            ),
        )


if __name__ == "__main__":
    unittest.main()
