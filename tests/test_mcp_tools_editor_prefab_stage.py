"""Contract tests for the Prefab Stage open / close wrappers (issue #236).

Pins the wrapper-side contract for ``editor_open_prefab`` and
``editor_close_prefab``:

* Open forwards the asset path to the bridge.
* Close save-true gates on the audit pair (``CHANGE_REASON_REQUIRED``).
* Close save-false bypasses the audit gate and forwards the flag.
* Close save-true forwards the audit pair and the save flag.

#222 Mode A: every exception-asserting test pairs the assertion with a
value-pin on the response envelope (code / severity / success / message
substring) so a partial regression surfaces in one diagnostic.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from prefab_sentinel import mcp_tools_editor_prefab_stage


def _success_envelope() -> dict:
    return {
        "success": True,
        "severity": "info",
        "code": "EDITOR_CTRL_PREFAB_STAGE_OPEN_OK",
        "message": "ok",
        "data": {"executed": True},
        "diagnostics": [],
    }


class EditorOpenPrefabTests(unittest.TestCase):
    """``editor_open_prefab`` forwards the asset path verbatim."""

    def test_open_prefab_forwards_the_asset_path_verbatim(self) -> None:
        with patch.object(
            mcp_tools_editor_prefab_stage, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_prefab_stage.editor_open_prefab(
                asset_path="Assets/Prefabs/Avatar.prefab",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        # Value-pin on action label + asset path together so a
        # regression that drops one surfaces both fields.
        self.assertEqual(
            ("open_prefab", "Assets/Prefabs/Avatar.prefab"),
            (kwargs["action"], kwargs["asset_path"]),
            msg=(
                "editor_open_prefab must reach the open_prefab action "
                "with the supplied asset path verbatim."
            ),
        )


class EditorClosePrefabAuditGateTests(unittest.TestCase):
    """``editor_close_prefab`` audit gating + save-flag forwarding."""

    def test_save_true_without_audit_rejected_pre_bridge(self) -> None:
        with patch.object(
            mcp_tools_editor_prefab_stage, "send_action",
            return_value=_success_envelope(),
        ) as send:
            response = mcp_tools_editor_prefab_stage.editor_close_prefab(
                save=True,
            )
        send.assert_not_called()
        self.assertEqual(
            ("CHANGE_REASON_REQUIRED", "error", False),
            (response["code"], response["severity"], response["success"]),
            msg=(
                "Save-true close without the audit pair must be "
                "rejected pre-bridge with CHANGE_REASON_REQUIRED."
            ),
        )

    def test_save_false_close_bypasses_audit_gate(self) -> None:
        with patch.object(
            mcp_tools_editor_prefab_stage, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_prefab_stage.editor_close_prefab(
                save=False,
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        # Save-false must reach the bridge with the save flag as False
        # so per-action semantics flow through the bridge consistently;
        # the wrapper must not rewrite the flag to True.
        self.assertEqual(
            ("close_prefab", False),
            (kwargs["action"], kwargs["save_on_close"]),
            msg=(
                "editor_close_prefab(save=False) must reach the "
                "close_prefab action with save_on_close=False and "
                "no audit pair required."
            ),
        )

    def test_save_true_with_audit_forwards_pair_and_flag(self) -> None:
        with patch.object(
            mcp_tools_editor_prefab_stage, "send_action",
            return_value=_success_envelope(),
        ) as send:
            mcp_tools_editor_prefab_stage.editor_close_prefab(
                save=True, confirm=True, change_reason="ship avatar v2",
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        # Save-true with the audit pair: the wrapper must forward the
        # save flag as True AND the audit pair to the bridge verbatim.
        self.assertEqual(
            ("close_prefab", True, True, "ship avatar v2"),
            (
                kwargs["action"], kwargs["save_on_close"],
                kwargs["confirm"], kwargs["change_reason"],
            ),
            msg=(
                "Save-true close with valid audit pair must forward "
                "save_on_close=True, confirm=True, and the audit "
                "reason verbatim."
            ),
        )


if __name__ == "__main__":
    unittest.main()
