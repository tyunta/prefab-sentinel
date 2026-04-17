"""Unity batchmode invocation path for runtime validation.

The single public entry point ``invoke_via_batchmode`` writes a request
JSON, spawns the configured Unity command, reads the response JSON, and
forwards the parsed envelope to the caller.  All Unity-side semantics
(success/code/diagnostics) come back through ``protocol.parse_runtime_response``.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from prefab_sentinel.contracts import ToolResponse, error_response
from prefab_sentinel.json_io import dump_json, load_json_file
from prefab_sentinel.services.runtime_validation.config import (
    RUNTIME_PROTOCOL_VERSION,
    build_runtime_command,
    failure_code,
    load_runtime_config,
    skip_response,
)
from prefab_sentinel.services.runtime_validation.protocol import (
    parse_runtime_response,
)


def _skip_envelope(
    *,
    action: str,
    target_root: Path,
    scene_path: str | None,
    profile: str | None,
    relative_fn: Callable[[Path], str],
) -> ToolResponse:
    skip_code = (
        "RUN_COMPILE_SKIPPED" if action == "compile_udonsharp" else "RUN_CLIENTSIM_SKIPPED"
    )
    skip_message = (
        "compile_udonsharp skipped because Unity batchmode execution is not configured."
        if action == "compile_udonsharp"
        else "run_clientsim skipped because Unity batchmode execution is not configured."
    )
    skip_data: dict[str, object] = {"project_root": relative_fn(target_root)}
    if scene_path is not None:
        skip_data["scene_path"] = scene_path
    if profile is not None:
        skip_data["profile"] = profile
    return skip_response(code=skip_code, message=skip_message, data=skip_data)


def invoke_via_batchmode(
    *,
    action: str,
    target_root: Path,
    scene_path: str | None,
    profile: str | None,
    relative_fn: Callable[[Path], str],
) -> ToolResponse:
    """Run *action* against the configured Unity batchmode command.

    When ``UNITY_COMMAND_ENV`` is unset, returns the action-specific
    ``RUN_*_SKIPPED`` envelope; on configuration errors, the
    ``RUN_CONFIG_ERROR`` envelope from ``load_runtime_config`` propagates;
    on subprocess failure, the action-specific ``RUN_COMPILE_FAILED`` /
    ``RUN002`` envelope carries the captured stdio.
    """
    config, config_error = load_runtime_config(default_project_root=target_root)
    if config_error is not None:
        return config_error
    if config is None:
        return _skip_envelope(
            action=action,
            target_root=target_root,
            scene_path=scene_path,
            profile=profile,
            relative_fn=relative_fn,
        )

    with tempfile.TemporaryDirectory(prefix="prefab-sentinel-runtime-") as temp_dir:
        temp_root = Path(temp_dir)
        request_path = temp_root / "request.json"
        response_path = temp_root / "response.json"
        payload = {
            "protocol_version": RUNTIME_PROTOCOL_VERSION,
            "action": action,
            "project_root": str(target_root),
            "scene_path": scene_path or "",
            "profile": profile or "",
            "timeout_sec": int(config["timeout_sec"]),
        }
        request_path.write_text(
            dump_json(payload, indent=None),
            encoding="utf-8",
        )
        command = build_runtime_command(
            config=config,
            request_path=request_path,
            response_path=response_path,
        )

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=int(config["timeout_sec"]),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            timeout_message = (
                "Unity batchmode compile timed out."
                if action == "compile_udonsharp"
                else "Unity ClientSim batchmode execution timed out."
            )
            return error_response(
                failure_code(action),
                timeout_message,
                data={
                    "action": action,
                    "project_root": relative_fn(target_root),
                    "scene_path": scene_path,
                    "profile": profile,
                    "command": command,
                    "timeout_sec": int(config["timeout_sec"]),
                    "error": str(exc),
                    "log_path": relative_fn(Path(config["log_path"])),
                    "read_only": False,
                    "executed": False,
                },
            )
        except OSError as exc:
            spawn_message = (
                "Failed to start Unity batchmode compile process."
                if action == "compile_udonsharp"
                else "Failed to start Unity ClientSim batchmode process."
            )
            return error_response(
                failure_code(action),
                spawn_message,
                data={
                    "action": action,
                    "project_root": relative_fn(target_root),
                    "scene_path": scene_path,
                    "profile": profile,
                    "command": command,
                    "error": str(exc),
                    "log_path": relative_fn(Path(config["log_path"])),
                    "read_only": False,
                    "executed": False,
                },
            )

        response_error: str | None = None
        if response_path.exists():
            try:
                response_payload = load_json_file(response_path)
            except (OSError, json.JSONDecodeError) as exc:
                response_payload = None
                response_error = str(exc)
        else:
            response_payload = None

        if response_payload is not None:
            response = parse_runtime_response(
                response_payload,
                action=action,
                project_root=target_root,
                scene_path=scene_path,
                profile=profile,
                log_path=Path(config["log_path"]),
                relative_fn=relative_fn,
            )
            if completed.returncode != 0 and response.success:
                return error_response(
                    "RUN_PROTOCOL_ERROR",
                    "Unity runtime returned success payload but exited with a non-zero code.",
                    data={
                        "action": action,
                        "project_root": relative_fn(target_root),
                        "scene_path": scene_path,
                        "profile": profile,
                        "returncode": completed.returncode,
                        "stdout": completed.stdout,
                        "stderr": completed.stderr,
                        "log_path": relative_fn(Path(config["log_path"])),
                        "read_only": False,
                        "executed": True,
                    },
                )
            return response

        invalid_message = (
            "Unity batchmode compile did not produce a valid response."
            if action == "compile_udonsharp"
            else "Unity ClientSim batchmode execution did not produce a valid response."
        )
        return error_response(
            failure_code(action),
            invalid_message,
            data={
                "action": action,
                "project_root": relative_fn(target_root),
                "scene_path": scene_path,
                "profile": profile,
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "response_error": response_error,
                "response_path": str(response_path),
                "log_path": relative_fn(Path(config["log_path"])),
                "read_only": False,
                "executed": completed.returncode == 0,
            },
        )
