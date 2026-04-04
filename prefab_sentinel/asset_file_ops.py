"""Asset file copy / rename with automatic m_Name synchronisation."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from prefab_sentinel.contracts import error_dict as _error_dict, success_dict as _success_dict
from prefab_sentinel.unity_assets import (
    decode_text_file,
    is_unity_text_asset,
)
from prefab_sentinel.unity_assets_path import resolve_asset_path
from prefab_sentinel.wsl_compat import to_wsl_path

_M_NAME_PATTERN = re.compile(r"(  m_Name: ).*")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _rewrite_m_name(text: str, new_name: str) -> tuple[str, str | None, str]:
    """Replace the first ``m_Name`` value in *text*.

    Returns ``(new_text, old_name_or_None, new_name)``.
    """
    m = _M_NAME_PATTERN.search(text)
    if m is None:
        return text, None, new_name
    old_name = text[m.end(1) : m.end()]
    if old_name == new_name:
        return text, old_name, new_name
    replaced = text[: m.end(1)] + new_name + text[m.end() :]
    return replaced, old_name, new_name


def _generate_guid() -> str:
    return uuid.uuid4().hex


def _generate_meta_content(guid: str) -> str:
    return f"fileFormatVersion: 2\nguid: {guid}\n"


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------


def copy_asset(
    source_path: str,
    dest_path: str,
    *,
    dry_run: bool = True,
    project_root: Path | None = None,
) -> dict:
    """Copy a Unity text asset, rewriting ``m_Name`` and generating a new ``.meta``."""
    src = resolve_asset_path(source_path, project_root)
    if not src.is_file():
        return _error_dict(
            "ASSET_COPY_SOURCE_NOT_FOUND",
            f"Source file not found: {source_path}",
        )
    if not is_unity_text_asset(src):
        return _error_dict(
            "ASSET_OP_UNSUPPORTED_TYPE",
            f"Unsupported asset type: {src.suffix}",
        )

    # Source .meta check — missing is a warning, not a blocker
    src_meta = Path(str(src) + ".meta")
    diagnostics: list[dict] = []
    if not src_meta.is_file():
        diagnostics.append({
            "detail": "source_meta_missing",
            "evidence": f"Source .meta not found: {src_meta}",
        })

    dest = Path(to_wsl_path(dest_path))
    if not dest.is_absolute() and project_root is not None:
        dest = project_root / dest
    if not dest.parent.is_dir():
        return _error_dict(
            "ASSET_COPY_DEST_DIR_NOT_FOUND",
            f"Destination directory not found: {dest.parent}",
        )
    if dest.exists():
        return _error_dict(
            "ASSET_COPY_DEST_EXISTS",
            f"Destination already exists: {dest_path}",
        )

    text = decode_text_file(src)
    new_stem = dest.stem
    new_text, old_name, new_name = _rewrite_m_name(text, new_stem)
    if old_name is None:
        diagnostics.append({
            "detail": "m_name_not_found",
            "evidence": f"No m_Name field found in {src.name}",
        })

    data: dict[str, object] = {
        "source_path": str(src),
        "dest_path": str(dest),
        "m_name_before": old_name,
        "m_name_after": new_name,
    }
    if old_name is not None and old_name == new_name:
        data["m_name_unchanged"] = True

    if dry_run:
        return _success_dict(
            "ASSET_COPY_DRY_RUN",
            f"Would copy {src.name} → {dest.name}",
            data=data,
            diagnostics=diagnostics,
        )

    try:
        dest.write_text(new_text, encoding="utf-8")
        new_guid = _generate_guid()
        dest_meta = Path(str(dest) + ".meta")
        dest_meta.write_text(_generate_meta_content(new_guid), encoding="utf-8")
    except OSError as exc:
        return _error_dict(
            "ASSET_OP_WRITE_FAILED",
            f"Write failed: {exc}",
        )

    data["new_guid"] = new_guid
    data["meta_created"] = True
    return _success_dict(
        "ASSET_COPY_APPLIED",
        f"Copied {src.name} → {dest.name}",
        data=data,
        diagnostics=diagnostics,
    )


def rename_asset(
    asset_path: str,
    new_name: str,
    *,
    dry_run: bool = True,
    project_root: Path | None = None,
) -> dict:
    """Rename a Unity text asset, rewriting ``m_Name`` and renaming ``.meta``."""
    src = resolve_asset_path(asset_path, project_root)
    if not src.is_file():
        return _error_dict(
            "ASSET_RENAME_NOT_FOUND",
            f"Asset not found: {asset_path}",
        )
    if not is_unity_text_asset(src):
        return _error_dict(
            "ASSET_OP_UNSUPPORTED_TYPE",
            f"Unsupported asset type: {src.suffix}",
        )

    new_path = src.parent / new_name
    if new_path.suffix.lower() != src.suffix.lower():
        return _error_dict(
            "ASSET_RENAME_EXT_MISMATCH",
            f"Extension mismatch: {src.suffix} → {new_path.suffix}",
        )
    if new_path.exists():
        return _error_dict(
            "ASSET_RENAME_DEST_EXISTS",
            f"Destination already exists: {new_name}",
        )

    text = decode_text_file(src)
    new_stem = new_path.stem
    new_text, old_name, applied_name = _rewrite_m_name(text, new_stem)

    diagnostics: list[dict] = []
    if old_name is None:
        diagnostics.append({
            "detail": "m_name_not_found",
            "evidence": f"No m_Name field found in {src.name}",
        })
    data: dict[str, object] = {
        "asset_path": str(src),
        "new_path": str(new_path),
        "m_name_before": old_name,
        "m_name_after": applied_name,
    }
    if old_name is not None and old_name == applied_name:
        data["m_name_unchanged"] = True

    if dry_run:
        return _success_dict(
            "ASSET_RENAME_DRY_RUN",
            f"Would rename {src.name} → {new_name}",
            data=data,
            diagnostics=diagnostics,
        )

    try:
        # Write updated content THEN rename (order matters)
        src.write_text(new_text, encoding="utf-8")
        src.rename(new_path)
    except OSError as exc:
        return _error_dict(
            "ASSET_OP_WRITE_FAILED",
            f"Rename failed: {exc}",
        )

    src_meta = Path(str(src) + ".meta")
    meta_renamed = False
    if src_meta.is_file():
        try:
            src_meta.rename(Path(str(new_path) + ".meta"))
            meta_renamed = True
        except OSError as exc:
            diagnostics.append({
                "detail": "meta_rename_failed",
                "evidence": str(exc),
            })

    data["meta_renamed"] = meta_renamed
    return _success_dict(
        "ASSET_RENAME_APPLIED",
        f"Renamed {src.name} → {new_name}",
        data=data,
        diagnostics=diagnostics,
    )
