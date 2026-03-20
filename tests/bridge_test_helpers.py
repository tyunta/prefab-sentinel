"""Shared test helpers for Unity bridge and runtime validation tests.

Provides:
* ``write_file``  – create a file with parent directory creation
* ``write_fake_runtime_runner`` – write a Python script that mimics the
  Unity runtime validation bridge, responding to ``compile_udonsharp``
  and ``run_clientsim`` actions with structured JSON.
"""

from __future__ import annotations

from pathlib import Path

_FAKE_RUNTIME_RUNNER_SCRIPT = """\
import json
import sys
from pathlib import Path

args = sys.argv[1:]

def arg_value(name: str) -> str:
    for index, value in enumerate(args[:-1]):
        if value == name:
            return args[index + 1]
    raise SystemExit(f"missing argument: {name}")

request_path = Path(arg_value("-sentinelRuntimeRequest"))
response_path = Path(arg_value("-sentinelRuntimeResponse"))
request = json.loads(request_path.read_text(encoding="utf-8"))
action = request.get("action", "")

if action == "compile_udonsharp":
    payload = {
        "success": True,
        "severity": "info",
        "code": "RUN_COMPILE_OK",
        "message": "compile ok",
        "data": {
            "udon_program_count": 3,
            "executed": True,
            "read_only": False,
        },
        "diagnostics": [],
    }
elif action == "run_clientsim":
    payload = {
        "success": True,
        "severity": "info",
        "code": "RUN_CLIENTSIM_OK",
        "message": "clientsim ok",
        "data": {
            "clientsim_ready": True,
            "executed": True,
            "read_only": False,
        },
        "diagnostics": [],
    }
else:
    payload = {
        "success": False,
        "severity": "error",
        "code": "RUN_PROTOCOL_ERROR",
        "message": f"unexpected action: {action}",
        "data": {
            "executed": False,
            "read_only": True,
        },
        "diagnostics": [],
    }

response_path.write_text(json.dumps(payload), encoding="utf-8")
"""


def write_file(path: Path, content: str) -> None:
    """Write *content* to *path*, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_fake_runtime_runner(path: Path) -> None:
    """Write a fake Unity runtime validation runner script to *path*.

    The generated script reads a JSON request (via ``-sentinelRuntimeRequest``),
    dispatches on the ``action`` field, and writes a JSON response to the path
    given by ``-sentinelRuntimeResponse``.

    Supported actions: ``compile_udonsharp``, ``run_clientsim``.
    Unknown actions return an error payload.
    """
    path.write_text(_FAKE_RUNTIME_RUNNER_SCRIPT, encoding="utf-8")
