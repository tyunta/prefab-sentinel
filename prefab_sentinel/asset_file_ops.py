"""Asset file copy / rename with automatic m_Name synchronisation."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path, PureWindowsPath

from prefab_sentinel.contracts import error_dict as _error_dict, success_dict as _success_dict
from prefab_sentinel.unity_assets import (
    decode_text_file,
    is_unity_text_asset,
)
from prefab_sentinel.unity_assets_path import resolve_asset_path
from prefab_sentinel.unity_yaml_parser import split_yaml_blocks
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


def _quoted_scalar_end(text: str, start: int) -> int | None:
    """Locate a quoted scalar's closing delimiter without decoding its value."""
    quote = text[start]
    position = start + 1
    while position < len(text):
        character = text[position]
        if quote == '"' and character == "\\":
            position += 2
        elif character == quote:
            if quote == "'" and text[position + 1:position + 2] == "'":
                position += 2
            else:
                return position + 1
        else:
            position += 1
    return None


def _flow_value_end(text: str, start: int) -> int | None:
    """Skip a flow mapping/sequence, including quoted values and nested flows."""
    openings = [text[start]]
    position = start + 1
    plain = False
    while position < len(text):
        character = text[position]
        separator = character in ",[]{}" or (
            character == ":" and text[position + 1:position + 2].isspace()
        )
        if plain and not separator:
            position += 1
            continue
        plain = False
        if character.isspace() or character in ":,":
            position += 1
        elif character in "\"'":
            end = _quoted_scalar_end(text, position)
            if end is None:
                return None
            position = end
        elif character in "[{":
            openings.append(character)
            position += 1
        elif character in "]}":
            if openings.pop() != ({"}": "{", "]": "["}[character]):
                return None
            position += 1
            if not openings:
                return position
        else:
            plain = True
    return None


def _controller_root_names(text: str) -> list[re.Match[str]] | None:
    """Find root name fields while preserving unrelated scalar boundaries."""
    # A sequence item's quoted or flow value may contain colons. Do not let
    # the plain-key branch backtrack over its opening delimiter or sequence marker.
    field_pattern = re.compile(
        r'^(?:(?P<indent>[ \t]*)(?P<sequence>-[ \t]+)?'
        r"""(?P<key>"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:''|[^'\r\n])*'"""
        r"""|(?![ \t"'\[{]|-[ \t])[^:\r\n]+):[ \t]*"""
        r'|(?P<item_indent>[ \t]*)-[ \t]+)',
        re.MULTILINE,
    )
    name_pattern = re.compile(r"^  m_Name: ([^\r\n]*)\r?$", re.MULTILINE)
    names: list[re.Match[str]] = []
    position = 0
    while field := field_pattern.search(text, position):
        is_root_name = (
            field.group("indent") == "  "
            and field.group("sequence") is None
            and field.group("key") == "m_Name"
        )
        if is_root_name:
            name = name_pattern.match(text, field.start())
            if name is None:
                return None
            names.append(name)
        value_start = field.end()
        if value_start == len(text):
            break
        if text[value_start] in "\"'":
            end = _quoted_scalar_end(text, value_start)
        elif text[value_start] in "[{":
            end = _flow_value_end(text, value_start)
        else:
            line_end = text.find("\n", value_start)
            end = len(text) if line_end < 0 else line_end
            # Plain scalar continuation is determined by indentation. Quotes
            # occurring inside that text do not open a different scalar.
            if text[value_start:end].strip():
                indent = len(field.group("indent") or field.group("item_indent") or "")
                continuation_end = end + 1
                for line in text[continuation_end:].splitlines(keepends=True):
                    if line.strip() and len(line) - len(line.lstrip()) <= indent:
                        break
                    continuation_end += len(line)
                    if line.strip():
                        end = continuation_end
        if end is None:
            return None
        if is_root_name and "\n" in text[value_start:end]:
            return None
        position = end
    return names


def _rewrite_controller_m_name(
    text: str, new_name: str,
    *, invalid_code: str, input_field: str,
) -> tuple[str, str, dict[str, str]] | dict:
    """Rewrite the uniquely identified Controller root name (Issues #228/#239)."""
    blocks = split_yaml_blocks(text)
    # Unity's YAML Class ID Reference identifies AnimatorController as 91;
    # document order and the first m_Name do not identify the main object.
    controllers = [block for block in blocks if block.class_id == "91" and not block.is_stripped]

    def invalid(reason: str) -> dict:
        return _error_dict(
            invalid_code,
            "Controller main object cannot be identified safely.",
            data={
                "field": input_field, "reason": reason,
                "matching_document_count": len(controllers),
            },
        )

    if len(controllers) != 1:
        return invalid("main_object_not_unique")
    controller = controllers[0]
    if (
        controller.text.splitlines()[1:2] != ["AnimatorController:"]
        or sum(block.file_id == controller.file_id for block in blocks) != 1
    ):
        return invalid("main_object_identity_invalid")

    names = _controller_root_names(controller.text)
    if names is None or len(names) != 1:
        return invalid("root_name_invalid")
    name = names[0]
    old_name = name.group(1)
    if not old_name.strip() or old_name[0] in "|>&*!{[":
        return invalid("root_name_invalid")
    if old_name[0] in "\"'" and (len(old_name) < 2 or old_name[-1] != old_name[0]):
        return invalid("root_name_invalid")
    following = next(
        (line for line in controller.text[name.end():].splitlines() if line.strip()), "",
    )
    if len(following) - len(following.lstrip()) > 2:
        return invalid("root_name_invalid")

    # Splice just this scalar; leave every other document and reference intact.
    # JSON quoting is valid YAML 1.1. Preserve plain/canonical-quoted no-ops
    # without claiming to decode every YAML source scalar grammar.
    quoted_name = json.dumps(new_name, ensure_ascii=False)
    new_scalar = old_name if old_name in (new_name, quoted_name) else quoted_name
    block_start = text.index(controller.text)
    start, end = block_start + name.start(1), block_start + name.end(1)
    return text[:start] + new_scalar + text[end:], old_name, {
        "class_id": controller.class_id,
        "file_id": controller.file_id,
        "type_name": "AnimatorController",
        "property_path": "m_Name",
    }


def _generate_guid() -> str:
    return uuid.uuid4().hex


def _generate_meta_content(guid: str) -> str:
    return f"fileFormatVersion: 2\nguid: {guid}\n"


def _path_resolution_error(
    code: str,
    message: str,
    *,
    detail: str,
    input_path: str,
    normalized_candidate_path: Path,
    resolution_root: Path | None,
    reason: str,
) -> dict:
    data = {
        "input_path": input_path,
        "normalized_candidate_path": str(normalized_candidate_path),
        "resolution_root": str(resolution_root) if resolution_root is not None else "",
        "reason": reason,
    }
    return _error_dict(
        code,
        message,
        data=data,
        diagnostics=[{"detail": detail, "evidence": f"reason={reason}"}],
    )

def _copy_source_resolution_error(
    code: str,
    message: str,
    *,
    input_path: str,
    normalized_candidate_path: Path,
    resolution_root: Path | None,
    reason: str,
) -> dict:
    return _path_resolution_error(
        code,
        message,
        detail="copy_source_resolution",
        input_path=input_path,
        normalized_candidate_path=normalized_candidate_path,
        resolution_root=resolution_root,
        reason=reason,
    )


def _rename_source_resolution_error(
    code: str,
    message: str,
    *,
    input_path: str,
    normalized_candidate_path: Path,
    resolution_root: Path | None,
    reason: str,
) -> dict:
    return _path_resolution_error(
        code,
        message,
        detail="rename_source_resolution",
        input_path=input_path,
        normalized_candidate_path=normalized_candidate_path,
        resolution_root=resolution_root,
        reason=reason,
    )


def _copy_dest_resolution_error(
    code: str,
    message: str,
    *,
    input_path: str,
    normalized_candidate_path: Path,
    resolution_root: Path | None,
    reason: str,
) -> dict:
    return _path_resolution_error(
        code,
        message,
        detail="copy_dest_resolution",
        input_path=input_path,
        normalized_candidate_path=normalized_candidate_path,
        resolution_root=resolution_root,
        reason=reason,
    )


def _cleanup_failure_diagnostic(detail: str, path: Path, exc: OSError) -> dict[str, str]:
    return {"detail": detail, "evidence": f"{path}: {exc}"}


def _path_status_failure_diagnostic(detail: str, path: Path, exc: OSError) -> dict[str, str]:
    return {"detail": detail, "evidence": f"{path}: {exc}"}


def _path_status_error(
    code: str,
    message: str,
    *,
    detail: str,
    path: Path,
    exc: OSError,
) -> dict:
    return _error_dict(
        code,
        message,
        diagnostics=[_path_status_failure_diagnostic(detail, path, exc)],
    )


def _unlink_for_cleanup(
    path: Path,
    diagnostics: list[dict[str, str]],
    *,
    detail: str,
) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        diagnostics.append(_cleanup_failure_diagnostic(detail, path, exc))


def _is_bare_asset_filename(name: str) -> bool:
    if name in {"", ".", ".."}:
        return False
    if "/" in name or "\\" in name:
        return False
    windows_path = PureWindowsPath(name)
    if windows_path.drive or windows_path.root:
        return False
    candidate = Path(name)
    return candidate.parent == Path(".") and candidate.name == name


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
    source_candidate = Path(to_wsl_path(source_path))
    root_candidate = Path(project_root) if project_root is not None else None
    try:
        root_abs = root_candidate.resolve() if root_candidate is not None else None
    except OSError:
        return _copy_source_resolution_error(
            "ASSET_COPY_SOURCE_INVALID_PATH",
            f"Invalid source path: {source_path}",
            input_path=source_path,
            normalized_candidate_path=source_candidate,
            resolution_root=root_candidate,
            reason="resolve_error",
        )

    if root_abs is not None and source_candidate.is_absolute():
        try:
            normalized_candidate = source_candidate.resolve()
        except OSError:
            return _copy_source_resolution_error(
                "ASSET_COPY_SOURCE_INVALID_PATH",
                f"Invalid source path: {source_path}",
                input_path=source_path,
                normalized_candidate_path=source_candidate,
                resolution_root=root_abs,
                reason="resolve_error",
            )
        reason = "absolute_path"
        if not normalized_candidate.is_relative_to(root_abs):
            reason = "outside_project"
        return _copy_source_resolution_error(
            "ASSET_COPY_SOURCE_INVALID_PATH",
            f"Invalid source path: {source_path}",
            input_path=source_path,
            normalized_candidate_path=normalized_candidate,
            resolution_root=root_abs,
            reason=reason,
        )

    try:
        src = resolve_asset_path(source_path, project_root)
    except (ValueError, OSError) as exc:
        reason = "resolve_error" if isinstance(exc, OSError) else "outside_project"
        normalized_candidate = source_candidate
        if root_abs is not None:
            normalized_candidate = root_abs / source_candidate
        if reason == "outside_project":
            try:
                normalized_candidate = normalized_candidate.resolve()
            except OSError:
                reason = "resolve_error"
        return _copy_source_resolution_error(
            "ASSET_COPY_SOURCE_INVALID_PATH",
            f"Invalid source path: {source_path}",
            input_path=source_path,
            normalized_candidate_path=normalized_candidate,
            resolution_root=root_abs,
            reason=reason,
        )
    try:
        source_exists = src.is_file()
    except OSError:
        return _copy_source_resolution_error(
            "ASSET_COPY_SOURCE_NOT_FOUND",
            f"Source file not found: {source_path}",
            input_path=source_path,
            normalized_candidate_path=src,
            resolution_root=root_abs,
            reason="status_error",
        )
    if not source_exists:
        return _copy_source_resolution_error(
            "ASSET_COPY_SOURCE_NOT_FOUND",
            f"Source file not found: {source_path}",
            input_path=source_path,
            normalized_candidate_path=src,
            resolution_root=root_abs,
            reason="not_found",
        )
    if not is_unity_text_asset(src):
        return _error_dict(
            "ASSET_OP_UNSUPPORTED_TYPE",
            f"Unsupported asset type: {src.suffix}",
        )

    src_meta = Path(str(src) + ".meta")
    diagnostics: list[dict[str, str]] = []
    try:
        source_meta_exists = src_meta.is_file()
    except OSError as exc:
        source_meta_exists = False
        diagnostics.append(
            _path_status_failure_diagnostic("source_meta_status_failed", src_meta, exc)
        )
    if not source_meta_exists:
        diagnostics.append({
            "detail": "source_meta_missing",
            "evidence": f"Source .meta not found: {src_meta}",
        })

    dest_candidate = Path(to_wsl_path(dest_path))
    if root_abs is not None:
        try:
            if dest_candidate.is_absolute():
                normalized_dest = dest_candidate.resolve()
            else:
                normalized_dest = (root_abs / dest_candidate).resolve()
        except OSError:
            return _copy_dest_resolution_error(
                "ASSET_COPY_DEST_INVALID_PATH",
                f"Invalid destination path: {dest_path}",
                input_path=dest_path,
                normalized_candidate_path=dest_candidate,
                resolution_root=root_abs,
                reason="resolve_error",
            )
        if not normalized_dest.is_relative_to(root_abs):
            return _copy_dest_resolution_error(
                "ASSET_COPY_DEST_INVALID_PATH",
                f"Invalid destination path: {dest_path}",
                input_path=dest_path,
                normalized_candidate_path=normalized_dest,
                resolution_root=root_abs,
                reason="outside_project_root",
            )
        dest = normalized_dest
    else:
        dest = dest_candidate
    try:
        dest_parent_exists = dest.parent.is_dir()
    except OSError as exc:
        return _path_status_error(
            "ASSET_COPY_DEST_DIR_NOT_FOUND",
            f"Destination directory not found: {dest.parent}",
            detail="copy_dest_dir_status_failed",
            path=dest.parent,
            exc=exc,
        )
    if not dest_parent_exists:
        return _error_dict(
            "ASSET_COPY_DEST_DIR_NOT_FOUND",
            f"Destination directory not found: {dest.parent}",
        )
    dest_meta = Path(str(dest) + ".meta")
    try:
        dest_exists = dest.exists()
        dest_meta_exists = dest_meta.exists()
    except OSError as exc:
        return _path_status_error(
            "ASSET_OP_WRITE_FAILED",
            f"Destination status check failed: {exc}",
            detail="copy_dest_status_failed",
            path=dest,
            exc=exc,
        )
    if dest_exists or dest_meta_exists:
        return _error_dict(
            "ASSET_COPY_DEST_EXISTS",
            f"Destination already exists: {dest_path}",
        )

    text = decode_text_file(src)
    new_stem = dest.stem
    old_name: str | None
    name_target = None
    if src.suffix.lower() == ".controller":
        controller_name = _rewrite_controller_m_name(
            text, new_stem,
            invalid_code="ASSET_COPY_MAIN_OBJECT_INVALID", input_field="source_path",
        )
        if isinstance(controller_name, dict):
            return controller_name
        new_text, old_name, name_target = controller_name
        new_name = new_stem
    else:
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
    if name_target is not None:
        data["m_name_target"] = name_target
    if old_name is not None and new_text == text:
        data["m_name_unchanged"] = True

    if dry_run:
        return _success_dict(
            "ASSET_COPY_DRY_RUN",
            f"Would copy {src.name} → {dest.name}",
            data=data,
            diagnostics=diagnostics,
        )

    asset_tmp = dest.with_name(f".{dest.name}.{_generate_guid()}.tmp")
    meta_tmp = dest.with_name(f".{dest.name}.{_generate_guid()}.meta")
    asset_committed = False
    meta_committed = False
    try:
        asset_tmp.write_text(new_text, encoding="utf-8")
        new_guid = _generate_guid()
        meta_tmp.write_text(_generate_meta_content(new_guid), encoding="utf-8")
        asset_tmp.replace(dest)
        asset_committed = True
        meta_tmp.replace(dest_meta)
        meta_committed = True
    except OSError as exc:
        cleanup_diagnostics: list[dict[str, str]] = []
        for created_path in (asset_tmp, meta_tmp):
            _unlink_for_cleanup(
                created_path,
                cleanup_diagnostics,
                detail="copy_cleanup_failed",
            )
        if asset_committed:
            _unlink_for_cleanup(
                dest,
                cleanup_diagnostics,
                detail="copy_rollback_cleanup_failed",
            )
        if meta_committed:
            _unlink_for_cleanup(
                dest_meta,
                cleanup_diagnostics,
                detail="copy_rollback_cleanup_failed",
            )
        return _error_dict(
            "ASSET_OP_WRITE_FAILED",
            f"Write failed: {exc}",
            diagnostics=[*diagnostics, *cleanup_diagnostics],
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
    source_candidate = Path(to_wsl_path(asset_path))
    root_candidate = Path(project_root) if project_root is not None else None
    try:
        root_abs = root_candidate.resolve() if root_candidate is not None else None
    except OSError:
        return _rename_source_resolution_error(
            "ASSET_RENAME_INVALID_PATH",
            f"Invalid asset path: {asset_path}",
            input_path=asset_path,
            normalized_candidate_path=source_candidate,
            resolution_root=root_candidate,
            reason="resolve_error",
        )
    try:
        src = resolve_asset_path(asset_path, project_root)
    except (ValueError, OSError) as exc:
        reason = "resolve_error" if isinstance(exc, OSError) else "outside_project"
        normalized_candidate = source_candidate
        if root_abs is not None and not source_candidate.is_absolute():
            normalized_candidate = root_abs / source_candidate
        if reason == "outside_project":
            try:
                normalized_candidate = normalized_candidate.resolve()
            except OSError:
                reason = "resolve_error"
        return _rename_source_resolution_error(
            "ASSET_RENAME_INVALID_PATH",
            f"Invalid asset path: {asset_path}",
            input_path=asset_path,
            normalized_candidate_path=normalized_candidate,
            resolution_root=root_abs,
            reason=reason,
        )
    try:
        source_exists = src.is_file()
    except OSError:
        return _rename_source_resolution_error(
            "ASSET_RENAME_NOT_FOUND",
            f"Asset not found: {asset_path}",
            input_path=asset_path,
            normalized_candidate_path=src,
            resolution_root=root_abs,
            reason="status_error",
        )
    if not source_exists:
        return _error_dict(
            "ASSET_RENAME_NOT_FOUND",
            f"Asset not found: {asset_path}",
        )
    if not is_unity_text_asset(src):
        return _error_dict(
            "ASSET_OP_UNSUPPORTED_TYPE",
            f"Unsupported asset type: {src.suffix}",
        )
    if not _is_bare_asset_filename(new_name):
        return _error_dict(
            "ASSET_RENAME_INVALID_NAME",
            f"Invalid asset name: {new_name}",
            data={"input_name": new_name, "reason": "not_bare_filename"},
        )

    new_path = src.parent / new_name
    if root_abs is not None:
        try:
            new_path_resolved = new_path.resolve()
        except OSError as exc:
            return _error_dict(
                "ASSET_RENAME_INVALID_NAME",
                f"Invalid asset name: {new_name}",
                data={
                    "input_name": new_name,
                    "reason": "resolve_error",
                    "error": str(exc),
                },
            )
        if not new_path_resolved.is_relative_to(root_abs):
            return _error_dict(
                "ASSET_RENAME_INVALID_NAME",
                f"Invalid asset name: {new_name}",
                data={"input_name": new_name, "reason": "outside_project_root"},
            )
    if new_path.suffix.lower() != src.suffix.lower():
        return _error_dict(
            "ASSET_RENAME_EXT_MISMATCH",
            f"Extension mismatch: {src.suffix} → {new_path.suffix}",
        )
    src_meta = Path(str(src) + ".meta")
    dst_meta = Path(str(new_path) + ".meta")
    try:
        destination_exists = new_path.exists()
    except OSError as exc:
        return _path_status_error(
            "ASSET_OP_WRITE_FAILED",
            f"Destination status check failed: {exc}",
            detail="rename_dest_status_failed",
            path=new_path,
            exc=exc,
        )
    try:
        destination_meta_exists = dst_meta.exists()
    except OSError as exc:
        return _path_status_error(
            "ASSET_OP_WRITE_FAILED",
            f"Destination .meta status check failed: {exc}",
            detail="rename_dest_meta_status_failed",
            path=dst_meta,
            exc=exc,
        )
    if destination_exists or destination_meta_exists:
        return _error_dict(
            "ASSET_RENAME_DEST_EXISTS",
            f"Destination already exists: {new_name}",
        )

    text = decode_text_file(src)
    new_stem = new_path.stem
    old_name: str | None
    name_target = None
    if src.suffix.lower() == ".controller":
        controller_name = _rewrite_controller_m_name(
            text, new_stem,
            invalid_code="ASSET_RENAME_MAIN_OBJECT_INVALID", input_field="asset_path",
        )
        if isinstance(controller_name, dict):
            return controller_name
        new_text, old_name, name_target = controller_name
        applied_name = new_stem
    else:
        new_text, old_name, applied_name = _rewrite_m_name(text, new_stem)

    diagnostics: list[dict[str, str]] = []
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
    if name_target is not None:
        data["m_name_target"] = name_target
    if old_name is not None and new_text == text:
        data["m_name_unchanged"] = True

    try:
        source_meta_exists = src_meta.is_file()
    except OSError as exc:
        return _error_dict(
            "ASSET_OP_WRITE_FAILED",
            f"Source .meta status check failed: {exc}",
            data={**data, "meta_renamed": False},
            diagnostics=[
                *diagnostics,
                _path_status_failure_diagnostic("source_meta_status_failed", src_meta, exc),
            ],
        )
    if not source_meta_exists:
        return _error_dict(
            "ASSET_OP_WRITE_FAILED",
            f"Source .meta not found: {src_meta}",
            data={**data, "meta_renamed": False},
            diagnostics=[
                *diagnostics,
                {
                    "detail": "source_meta_missing",
                    "evidence": f"Source .meta not found: {src_meta}",
                },
            ],
        )

    if dry_run:
        return _success_dict(
            "ASSET_RENAME_DRY_RUN",
            f"Would rename {src.name} → {new_name}",
            data=data,
            diagnostics=diagnostics,
        )

    content_tmp = src.with_name(f".{new_path.name}.{_generate_guid()}.tmp")
    asset_renamed = False
    meta_renamed = False
    try:
        content_tmp.write_text(new_text, encoding="utf-8")
        src.rename(new_path)
        asset_renamed = True
        src_meta.rename(dst_meta)
        meta_renamed = True
        content_tmp.replace(new_path)
    except OSError as exc:
        rollback_diagnostics: list[dict[str, str]] = []
        if asset_renamed and not meta_renamed:
            failure_detail = "meta_rename_failed"
        elif meta_renamed:
            failure_detail = "content_replace_failed"
        else:
            failure_detail = "asset_rename_failed"
        rollback_diagnostics.append({"detail": failure_detail, "evidence": str(exc)})
        _unlink_for_cleanup(
            content_tmp,
            rollback_diagnostics,
            detail="rename_cleanup_failed",
        )
        if meta_renamed:
            try:
                dst_meta.rename(src_meta)
            except OSError as rollback_exc:
                rollback_diagnostics.append(
                    _cleanup_failure_diagnostic(
                        "meta_rollback_failed",
                        dst_meta,
                        rollback_exc,
                    )
                )
        if asset_renamed:
            try:
                new_path.rename(src)
            except OSError as rollback_exc:
                rollback_diagnostics.append(
                    _cleanup_failure_diagnostic(
                        "rename_rollback_failed",
                        new_path,
                        rollback_exc,
                    )
                )
        return _error_dict(
            "ASSET_OP_WRITE_FAILED",
            f"Rename failed: {exc}",
            data={**data, "meta_renamed": False},
            diagnostics=[*diagnostics, *rollback_diagnostics],
        )

    data["meta_renamed"] = True
    return _success_dict(
        "ASSET_RENAME_APPLIED",
        f"Renamed {src.name} → {new_name}",
        data=data,
        diagnostics=diagnostics,
    )
