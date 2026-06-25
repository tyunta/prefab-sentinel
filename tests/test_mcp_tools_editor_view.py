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


class EditorConsoleDeterministicCaptureTests(unittest.TestCase):
    def test_editor_console_since_sequence_reads_ring_buffer(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_view.editor_console(since_sequence=41, order="oldest_first")

        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_console_logs", 41, "oldest_first"),
            (kwargs["action"], kwargs["since_sequence"], kwargs["order"]),
            msg=f"console sequence selector forwarding mismatch: {kwargs!r}",
        )

    def test_editor_console_request_id_filters_related_logs(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_view.editor_console(since_request_id="0123456789abcdef0123456789abcdef")

        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            "0123456789abcdef0123456789abcdef",
            kwargs["since_request_id"],
            msg=f"console request-id selector forwarding mismatch: {kwargs!r}",
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


class EditorScreenshotPreflightFailureTests(unittest.TestCase):
    def test_oversized_dimensions_rejected_before_refresh_or_capture(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=_SCREENSHOT_BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                width=mcp_tools_editor_view.SCREENSHOT_DIMENSION_MAX + 1,
                height=64,
                refresh=True,
            )

        send.assert_not_called()
        self.assertEqual(
            (False, "error", "SCREENSHOT_DIMENSIONS_OUT_OF_RANGE"),
            (response["success"], response["severity"], response["code"]),
        )
        self.assertEqual(
            {
                "width": mcp_tools_editor_view.SCREENSHOT_DIMENSION_MAX + 1,
                "height": 64,
                "min": mcp_tools_editor_view.SCREENSHOT_DIMENSION_MIN,
                "max": mcp_tools_editor_view.SCREENSHOT_DIMENSION_MAX,
            },
            response["data"],
        )

    def test_refresh_failure_returns_without_capture_request(self) -> None:
        refresh_failure = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_REFRESH_FAILED",
            "message": "refresh failed",
            "data": {"phase": "refresh"},
            "diagnostics": [],
        }
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=refresh_failure,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(refresh=True)

        send.assert_called_once_with(action="refresh_asset_database")
        self.assertEqual(refresh_failure, response)


class EditorScreenshotAngleAllowlistTests(unittest.TestCase):
    """Issue #84 — ``editor_screenshot`` rejects an ``angle`` value
    outside the six-preset allowlist before any bridge transport
    activity, and the exported allowlist tuple pins the canonical
    six-name set in issue-body order.
    """

    _BRIDGE_OK = _SCREENSHOT_BRIDGE_OK

    def test_allowlist_constant_pins_exact_value(self) -> None:
        self.assertEqual(
            (
                "front",
                "three_quarter",
                "back",
                "right",
                "left",
                "top",
                "current_camera",
            ),
            mcp_tools_editor_view.SCREENSHOT_ANGLE_PRESETS,
            msg=(
                "Wrapper-side angle allowlist must include renderer presets "
                "and the UI-only current_camera selector in canonical order."
            ),
        )

    def test_unknown_angle_preset_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                target="/Avatar", angle="three_eighths", refresh=False,
            )
        send.assert_not_called()
        observed = (
            response["success"],
            response["severity"],
            response["code"],
            "three_eighths" in response["message"],
            "three_quarter" in response["message"],
        )
        self.assertEqual(
            (False, "error", "SCREENSHOT_ANGLE_INVALID", True, True),
            observed,
            msg=(
                "Unknown angle preset must yield SCREENSHOT_ANGLE_INVALID "
                "with a message naming the supplied value and at least "
                "one of the six allowed preset names."
            ),
        )

    def test_wrapper_rejection_on_target_path_suppresses_refresh(self) -> None:
        # Pre-bridge wrapper rejection on an angle-allowlist violation
        # must suppress the pre-screenshot refresh round-trip; the
        # wrapper performs no transport activity at all on rejection.
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                target="/Avatar", angle="bogus", refresh=True,
            )
        send.assert_not_called()
        self.assertEqual(
            "SCREENSHOT_ANGLE_INVALID", response["code"],
            msg=(
                "Rejected target/angle combination must suppress the "
                "pre-screenshot refresh round-trip (#84)."
            ),
        )


class EditorScreenshotTargetForwardingTests(unittest.TestCase):
    """Issue #84 — ``editor_screenshot`` target-oriented capture mode
    forwarding contract.

    Pins:

    * Default screenshot call carries neither ``target`` nor ``angle``
      on the bridge kwargs (no silent forwarding of the default
      ``angle="three_quarter"`` when ``target`` is empty).
    * Object-capture forwarding: both ``target`` and explicit ``angle``
      reach the bridge action verbatim.
    * ``view="game"`` + ``target`` is rejected pre-bridge with
      ``SCREENSHOT_TARGET_INVALID_VIEW``.
    * ``target`` + face-feature ``crop_roi`` preset is rejected with
      ``SCREENSHOT_TARGET_CROP_CONFLICT``.
    * ``target`` + pixel-rectangle ``crop_roi`` is accepted; both
      fields reach the bridge.
    """

    _BRIDGE_OK = _SCREENSHOT_BRIDGE_OK

    def test_default_args_produce_no_target_or_angle_on_kwargs(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(refresh=False)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_screenshot", False, False),
            (
                kwargs["action"],
                "target" in kwargs,
                "angle" in kwargs,
            ),
            msg=(
                "Default editor_screenshot() must carry neither "
                "``target`` nor ``angle`` on the bridge kwargs (no "
                "silent forwarding of the wrapper default angle when "
                "target is empty)."
            ),
        )

    def test_target_and_angle_both_reach_the_bridge_verbatim(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(
                target="/Avatar/Body", angle="three_quarter", refresh=False,
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_screenshot", "/Avatar/Body", "three_quarter"),
            (kwargs["action"], kwargs["target"], kwargs["angle"]),
            msg=(
                "target='/Avatar/Body' and angle='three_quarter' must "
                "reach the bridge unchanged on the capture_screenshot "
                "action."
            ),
        )

    def test_target_with_omitted_angle_leaves_bridge_default_unresolved(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(target="/Avatar/Body", refresh=False)

        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_screenshot", "/Avatar/Body", False),
            (kwargs["action"], kwargs["target"], "angle" in kwargs),
            msg="omitted target angle must stay unresolved until bridge target-mode routing.",
        )

    def test_view_game_with_target_is_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                view="game", target="/Avatar", refresh=False,
            )
        send.assert_not_called()
        observed = (
            response["success"],
            response["severity"],
            response["code"],
            "game" in response["message"],
            "scene" in response["message"],
        )
        self.assertEqual(
            (False, "error", "SCREENSHOT_TARGET_INVALID_VIEW", True, True),
            observed,
            msg=(
                "view='game' with target must yield "
                "SCREENSHOT_TARGET_INVALID_VIEW naming the supplied "
                "view and the Scene-view-only constraint."
            ),
        )

    def test_target_with_face_feature_crop_roi_is_rejected(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                target="/Avatar", crop_roi="auto_face", refresh=False,
            )
        send.assert_not_called()
        observed = (
            response["success"],
            response["severity"],
            response["code"],
            "auto_face" in response["message"],
            "/Avatar" in response["message"],
        )
        self.assertEqual(
            (False, "error", "SCREENSHOT_TARGET_CROP_CONFLICT", True, True),
            observed,
            msg=(
                "target + face-feature crop_roi must yield "
                "SCREENSHOT_TARGET_CROP_CONFLICT naming both supplied "
                "values."
            ),
        )

    def test_target_with_pixel_rectangle_crop_roi_passes_through(self) -> None:
        # Pixel-rectangle crop_roi is independent of the framing
        # re-frame, so the combination is explicitly accepted by
        # the spec; both fields must reach the bridge.
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(
                target="/Avatar",
                crop_roi="10,20,300,200",
                refresh=False,
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_screenshot", "/Avatar", "10,20,300,200"),
            (kwargs["action"], kwargs["target"], kwargs["crop_roi"]),
            msg=(
                "target + pixel-rectangle crop_roi must both reach the "
                "bridge on the capture_screenshot action (the rectangle "
                "is applied to the rendered frame after framing)."
            ),
        )


class EditorScreenshotFitModeTests(unittest.TestCase):
    """Issue #90 — target screenshot fit-mode validation and forwarding."""

    _BRIDGE_OK = _success_envelope()

    def test_invalid_fit_mode_is_rejected_before_refresh_or_capture(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                target="/Avatar", fit_mode="fill", refresh=True,
            )
        send.assert_not_called()
        observed = (
            response["success"],
            response["severity"],
            response["code"],
            response["data"]["supplied"],
            response["data"]["allowed_fit_modes"],
            "fill" in response["message"],
            "max_axis" in response["message"],
            "both_axes" in response["message"],
        )
        self.assertEqual(
            (
                False,
                "error",
                "SCREENSHOT_FIT_MODE_INVALID",
                "fill",
                ["max_axis", "both_axes"],
                True,
                True,
                True,
            ),
            observed,
            msg=(
                "Invalid fit_mode must fail at the wrapper boundary with "
                "the supplied value and the exact allowed selector set."
            ),
        )

    def test_target_fit_mode_reaches_target_capture_payload(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(
                target="/Avatar", angle="right", fit_mode="both_axes", refresh=False,
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_screenshot", "/Avatar", "right", "both_axes"),
            (kwargs["action"], kwargs["target"], kwargs["angle"], kwargs["fit_mode"]),
            msg="target screenshot fit_mode must be forwarded unchanged to the bridge.",
        )

    def test_no_target_screenshot_does_not_forward_fit_mode(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=self._BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(fit_mode="both_axes", refresh=False)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("capture_screenshot", False),
            (kwargs["action"], "fit_mode" in kwargs),
            msg="scene/game screenshot payloads must not receive target-only fit_mode.",
        )

    def test_registered_tool_surface_forwards_fit_mode_unchanged(self) -> None:
        registered_tools = {}

        class RecordingServer:
            def tool(self, name: str | None = None):
                def register(func):
                    registered_tools[name or func.__name__] = func
                    return func

                return register

        with patch.object(
            mcp_tools_editor_view,
            "editor_screenshot",
            return_value=self._BRIDGE_OK,
        ) as editor_screenshot:
            mcp_tools_editor_view.register_editor_view_tools(RecordingServer())
            response = registered_tools["editor_screenshot"](
                target="/Avatar", angle="right", fit_mode="both_axes", refresh=False,
            )

        self.assertIs(response, self._BRIDGE_OK)
        editor_screenshot.assert_called_once_with(
            view="scene",
            width=0,
            height=0,
            refresh=False,
            crop_roi="",
            target="/Avatar",
            angle="right",
            target_mode="auto",
            padding_ratio=0.10,
            projection="auto",
            fit_mode="both_axes",
        )


class EditorScreenshotUiFramingTests(unittest.TestCase):
    def test_world_space_ui_target_selectors_reach_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=_SCREENSHOT_BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(
                target="/Canvas/WatchingButton",
                target_mode="world_space_ui",
                projection="orthographic",
                padding_ratio=0.2,
                angle="front",
                refresh=False,
            )

        send.assert_called_once()
        self.assertEqual(
            (
                "capture_screenshot",
                "/Canvas/WatchingButton",
                "world_space_ui",
                "orthographic",
                0.2,
                "front",
            ),
            (
                send.call_args.kwargs["action"],
                send.call_args.kwargs["target"],
                send.call_args.kwargs["target_mode"],
                send.call_args.kwargs["projection"],
                send.call_args.kwargs["padding_ratio"],
                send.call_args.kwargs["angle"],
            ),
            msg=f"UI screenshot selectors were not forwarded: {send.call_args.kwargs!r}",
        )

    def test_world_space_ui_omitted_angle_leaves_bridge_default_unresolved(self) -> None:
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=_SCREENSHOT_BRIDGE_OK,
        ) as send:
            mcp_tools_editor_view.editor_screenshot(
                target="/Canvas/WatchingButton",
                target_mode="world_space_ui",
                refresh=False,
            )

        send.assert_called_once()
        self.assertEqual(
            ("capture_screenshot", "world_space_ui", False),
            (
                send.call_args.kwargs["action"],
                send.call_args.kwargs["target_mode"],
                "angle" in send.call_args.kwargs,
            ),
            msg="world_space_ui omitted angle must stay unresolved until bridge target-mode routing.",
        )

    def test_world_space_ui_current_camera_selector_and_metadata_are_preserved(self) -> None:
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_SCREENSHOT_OK",
            "message": "ok",
            "data": {
                "target_mode": "world_space_ui",
                "bounds_source": "rect_transform",
                "bounds_center": [1.0, 2.0, 3.0],
                "bounds_extents": [0.5, 0.25, 0.1],
                "ui_normal": [0.0, 0.0, 1.0],
                "camera_position": [1.0, 2.0, 8.0],
                "camera_look_at": [1.0, 2.0, 3.0],
                "camera_orthographic": True,
                "camera_size": 0.6,
                "projection": "orthographic",
            },
            "diagnostics": [],
        }
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=bridge_response,
        ) as send:
            response = mcp_tools_editor_view.editor_screenshot(
                target="/Canvas/WatchingButton",
                target_mode="world_space_ui",
                angle="current_camera",
                refresh=False,
            )

        send.assert_called_once()
        self.assertEqual("current_camera", send.call_args.kwargs["angle"])
        self.assertEqual(bridge_response, response)

    def test_ui_selector_validation_errors_are_typed(self) -> None:
        cases = [
            (
                {"target": "/Canvas", "target_mode": "screen_space"},
                "SCREENSHOT_TARGET_MODE_INVALID",
                "screen_space",
            ),
            (
                {"target": "/Canvas", "projection": "fisheye"},
                "SCREENSHOT_PROJECTION_INVALID",
                "fisheye",
            ),
            (
                {"target": "/Canvas", "padding_ratio": -0.01},
                "SCREENSHOT_PADDING_RATIO_INVALID",
                "-0.01",
            ),
        ]
        with patch.object(
            mcp_tools_editor_view, "send_action", return_value=_SCREENSHOT_BRIDGE_OK,
        ) as send:
            for kwargs, expected_code, expected_message_part in cases:
                with self.subTest(expected_code=expected_code):
                    response = mcp_tools_editor_view.editor_screenshot(
                        refresh=False,
                        **kwargs,
                    )
                    self.assertEqual(
                        (False, "error", expected_code, True),
                        (
                            response["success"],
                            response["severity"],
                            response["code"],
                            expected_message_part in response["message"],
                        ),
                        msg=f"unexpected selector error envelope: {response!r}",
                    )

        send.assert_not_called()


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
