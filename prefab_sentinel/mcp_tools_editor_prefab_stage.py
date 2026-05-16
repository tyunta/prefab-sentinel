"""MCP tools for Prefab Stage open / close (issue #236).

When a Prefab Stage is active, every hierarchy-bound bridge handler
resolves hierarchy paths against the stage root first and falls back to
the open scene only when the stage lookup returns nothing. Open / close
are dedicated Python wrappers so the audit boundary on the save path is
unbypassable from the public surface.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.mcp_validation import require_write_audit

__all__ = [
    "editor_open_prefab",
    "editor_close_prefab",
    "register_editor_prefab_stage_tools",
]


def editor_open_prefab(asset_path: str) -> dict[str, Any]:
    """Open the supplied prefab asset as the active Prefab Stage.

    Subsequent hierarchy-bound write operations resolve their hierarchy
    path against the stage root before falling back to the open scene.
    """
    return send_action(action="open_prefab", asset_path=asset_path)


def editor_close_prefab(
    save: bool = True,
    confirm: bool = False,
    change_reason: str = "",
) -> dict[str, Any]:
    """Close the active Prefab Stage; the save path requires audit (issue #236).

    The save path enforces the writer audit pair (``confirm=True`` AND a
    non-empty ``change_reason``) before contacting the bridge so a
    save-true close cannot bypass the auditor. ``save=False`` does not
    require the audit pair because nothing is persisted; the bridge is
    contacted unconditionally so the close-without-save semantics flow
    through the bridge consistently.
    """
    if save:
        audit_err = require_write_audit(
            "editor_close_prefab", confirm, change_reason,
        )
        if audit_err is not None:
            return audit_err
        # Bridge DTO names the flag ``save_on_close`` so the wire format
        # disambiguates the close-time save toggle from a generic save
        # action.  The Python keyword stays ``save`` for caller ergonomics.
        return send_action(
            action="close_prefab",
            save_on_close=True,
            confirm=True,
            change_reason=change_reason.strip(),
        )
    return send_action(action="close_prefab", save_on_close=False)


def register_editor_prefab_stage_tools(server: FastMCP) -> None:
    """Register the Prefab Stage open / close tools on *server*."""

    @server.tool(name="editor_open_prefab")
    def _editor_open_prefab(asset_path: str) -> dict[str, Any]:
        """Open a prefab as the active Prefab Stage (issue #236).

        Args:
            asset_path: Asset path of the prefab to open
                (e.g. ``Assets/Prefabs/Avatar.prefab``).
        """
        return editor_open_prefab(asset_path=asset_path)

    @server.tool(name="editor_close_prefab")
    def _editor_close_prefab(
        save: bool = True,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Close the active Prefab Stage (issue #236).

        Args:
            save: Persist changes back to the prefab asset before
                returning to the main stage. ``True`` (default) is the
                writer path and requires the audit pair; ``False`` does
                not require an audit reason.
            confirm: Required ``True`` on the save path.
            change_reason: Required non-empty audit reason on the save path.
        """
        return editor_close_prefab(
            save=save, confirm=confirm, change_reason=change_reason,
        )
