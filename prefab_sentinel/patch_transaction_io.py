"""Contained report persistence and exact-byte restoration for patch transactions."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from subprocess import TimeoutExpired, run as _run_process
from uuid import uuid4

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse

# Local create/link/close has no legitimate long-running phase, so preflight is bounded.
_RESERVATION_TIMEOUT_SECONDS = 10
_RESERVATION_STATUS_RESERVED = "reserved"
_RESERVATION_STATUS_OWNER_CREATE_FAILED = "owner_create_failed"
_RESERVATION_STATUS_REPORT_EXISTS = "report_exists"
_RESERVATION_STATUS_LINK_FAILED = "link_failed"
_RESERVATION_STATUS_RELEASE_FAILED = "release_failed"


def _emit_reservation_status(status: str) -> int:
    print(json.dumps({"status": status}), flush=True)
    return 0 if status == _RESERVATION_STATUS_RESERVED else 1


def _reservation_worker(owner_path: Path, report_path: Path) -> int:
    try:
        descriptor = os.open(
            owner_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except (OSError, ValueError):
        return _emit_reservation_status(_RESERVATION_STATUS_OWNER_CREATE_FAILED)

    try:
        os.link(owner_path, report_path)
    except FileExistsError:
        status = _RESERVATION_STATUS_REPORT_EXISTS
    except (OSError, ValueError):
        status = _RESERVATION_STATUS_LINK_FAILED
    else:
        status = _RESERVATION_STATUS_RESERVED

    try:
        os.close(descriptor)
    except (OSError, ValueError):
        status = _RESERVATION_STATUS_RELEASE_FAILED
    return _emit_reservation_status(status)


def _reservation_worker_main(arguments: list[str]) -> int:
    if len(arguments) != 3 or arguments[0] != "--reserve":
        return _emit_reservation_status(_RESERVATION_STATUS_OWNER_CREATE_FAILED)
    return _reservation_worker(Path(arguments[1]), Path(arguments[2]))


def _reservation_owner_path(report_path: Path) -> Path:
    return report_path.with_name(f".prefab-sentinel-reservation-{uuid4().hex}")


def _run_reservation_process(
    owner_path: Path,
    report_path: Path,
) -> tuple[bool, bool | None]:
    try:
        completed = _run_process(
            [
                sys.executable,
                "-m",
                "prefab_sentinel.patch_transaction_io",
                "--reserve",
                os.fspath(owner_path),
                os.fspath(report_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_RESERVATION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, ValueError, UnicodeDecodeError, TimeoutExpired):
        return False, None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False, None

    if completed.returncode == 0 and payload == {
        "status": _RESERVATION_STATUS_RESERVED,
    }:
        return True, True
    if completed.returncode != 1 or not isinstance(payload, dict):
        return False, None
    if payload == {"status": _RESERVATION_STATUS_OWNER_CREATE_FAILED}:
        return False, False
    if payload in (
        {"status": _RESERVATION_STATUS_REPORT_EXISTS},
        {"status": _RESERVATION_STATUS_LINK_FAILED},
        {"status": _RESERVATION_STATUS_RELEASE_FAILED},
    ):
        return False, True
    return False, None


def _same_reservation_inode(owner_path: Path, report_path: Path) -> bool | None:
    try:
        return owner_path.samefile(report_path)
    except FileNotFoundError:
        return False
    except OSError:
        return None


def _complete_report_reservation(owner_path: Path, report_path: Path) -> str | None:
    if _same_reservation_inode(owner_path, report_path) is not True:
        return "out_report reservation cleanup failed."
    try:
        is_empty = report_path.stat().st_size == 0
    except OSError:
        return "out_report reservation cleanup failed."
    if not is_empty or _cleanup_temp_file(owner_path) is not None:
        return "out_report reservation cleanup failed."
    return None


def _discard_failed_reservation(
    owner_path: Path,
    report_path: Path,
    *,
    owner_created: bool | None,
) -> str | None:
    if owner_created is False:
        return None
    if owner_created is None:
        try:
            owner_path.stat()
        except FileNotFoundError:
            return None
        except OSError:
            return "out_report reservation cleanup failed."
        return "out_report reservation cleanup failed."

    same_inode = _same_reservation_inode(owner_path, report_path)
    if same_inode is None:
        return "out_report reservation cleanup failed."
    if same_inode:
        if _cleanup_temp_file(owner_path) is not None:
            return "out_report reservation cleanup failed."
        if _cleanup_temp_file(report_path) is not None:
            return "out_report reservation cleanup failed."
        return None
    if _cleanup_temp_file(owner_path) is not None:
        return "out_report reservation cleanup failed."
    return None

def _reserve_empty_report(report_path: Path) -> str | None:
    owner_path = _reservation_owner_path(report_path)
    reserved, owner_created = _run_reservation_process(owner_path, report_path)
    if reserved:
        return _complete_report_reservation(owner_path, report_path)

    cleanup_error = _discard_failed_reservation(
        owner_path,
        report_path,
        owner_created=owner_created,
    )
    if cleanup_error is not None:
        return cleanup_error
    return "out_report could not be reserved."


def validate_transaction_report_path(
    project_root: Path,
    out_report: str | None,
) -> Path | ToolResponse:
    if not isinstance(out_report, str) or not out_report.strip():
        return _report_input_error(
            "OUT_REPORT_REQUIRED",
            "out_report is required when confirm=True.",
        )

    try:
        root = project_root.resolve(strict=True)
        candidate = Path(out_report)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved_candidate = candidate.resolve(strict=False)
    except (OSError, ValueError):
        return _report_input_error(
            "OUT_REPORT_WRITE_FAILED",
            "out_report parent could not be resolved.",
        )

    if not resolved_candidate.is_relative_to(root):
        return _report_input_error(
            "OUT_REPORT_OUTSIDE_PROJECT",
            "out_report must resolve inside the project root.",
        )

    try:
        parent = candidate.parent.resolve(strict=True)
    except (OSError, ValueError):
        return _report_input_error(
            "OUT_REPORT_WRITE_FAILED",
            "out_report parent could not be resolved.",
        )

    report_path = parent / candidate.name
    if not parent.is_dir():
        return _report_input_error(
            "OUT_REPORT_WRITE_FAILED",
            "out_report parent must be an existing directory.",
        )
    if not report_path.is_relative_to(root):
        return _report_input_error(
            "OUT_REPORT_OUTSIDE_PROJECT",
            "out_report must resolve inside the project root.",
        )
    return report_path


def reserve_transaction_report(
    project_root: Path,
    out_report: str | None,
) -> Path | ToolResponse:
    report_path = validate_transaction_report_path(project_root, out_report)
    if isinstance(report_path, ToolResponse):
        return report_path

    reservation_error = _reserve_empty_report(report_path)
    if reservation_error is not None:
        return _report_input_error("OUT_REPORT_WRITE_FAILED", reservation_error)
    return report_path


def write_report_payload(
    report_path: Path,
    payload: Mapping[str, object],
) -> None:
    content = json.dumps(dict(payload), indent=2, sort_keys=True) + "\n"
    _atomic_replace(report_path, content.encode("utf-8"))


def write_transaction_report(report_path: Path, response: ToolResponse) -> None:
    write_report_payload(report_path, response.to_dict())


def restore_transaction_preimage(target_path: Path, preimage: bytes) -> None:
    _atomic_replace(target_path, preimage)


def discard_transaction_report(report_path: Path) -> None:
    try:
        report_path.unlink()
    except FileNotFoundError:
        return


def _atomic_replace(destination: Path, content: bytes) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    except OSError as exc:
        cleanup_error = _cleanup_temp_file(temp_path)
        if cleanup_error is not None:
            raise OSError("atomic replace and temporary-file cleanup failed") from exc
        raise


def _cleanup_temp_file(temp_path: Path) -> OSError | None:
    try:
        temp_path.unlink()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return exc
    return None


def _report_input_error(code: str, message: str) -> ToolResponse:
    return ToolResponse(
        success=False,
        severity=Severity.ERROR,
        code=code,
        message=message,
        data={},
        diagnostics=[
            Diagnostic(
                path="",
                location="out_report",
                detail="invalid_field",
                evidence=message,
                severity="error",
            )
        ],
    )


if __name__ == "__main__":
    raise SystemExit(_reservation_worker_main(sys.argv[1:]))
