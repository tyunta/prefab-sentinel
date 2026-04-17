"""Cross-language constant drift checker (issue #84).

Compares three invariants between the Python side and the C# Editor
bridge side:

1. Version strings — ``pyproject.toml``, ``.claude-plugin/plugin.json``,
   and the ``BridgeVersion`` literal in
   ``tools/unity/PrefabSentinel.UnityEditorControlBridge.cs``.
2. Bridge protocol version integer — Python
   ``prefab_sentinel.bridge_constants.PROTOCOL_VERSION`` vs the C#
   ``ProtocolVersion`` literal.
3. Severity vocabulary — Python
   ``prefab_sentinel.bridge_constants.VALID_SEVERITIES`` vs every string
   literal assigned to a ``severity`` field on the C# side.

Exit codes
----------

``0`` — all three invariants align.
``1`` — one or more invariants mismatch (drift detected).
``2`` — an input file is missing, cannot be parsed, or a required
          constant key is absent from an otherwise-parseable file.

Invocation is intentionally dependency-free on the Python side beyond the
standard library so it can be called from both pre-commit hooks and CI.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

# ---------- Default file locations ----------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_DEFAULT_PLUGIN_JSON = _REPO_ROOT / ".claude-plugin" / "plugin.json"
_DEFAULT_CSHARP = _REPO_ROOT / "tools" / "unity" / "PrefabSentinel.UnityEditorControlBridge.cs"

# ---------- Regex patterns for C# constants -------------------------------

_RE_CSHARP_BRIDGE_VERSION = re.compile(r'BridgeVersion\s*=\s*"([^"]+)"')
_RE_CSHARP_PROTOCOL_VERSION = re.compile(r"ProtocolVersion\s*=\s*(\d+)")
_RE_CSHARP_SEVERITY = re.compile(r'severity\s*=\s*"([A-Za-z]+)"')


# ---------- Loader helpers (all return str/int/list or raise ``_LoadError``)

class _LoadError(Exception):
    """Raised when an input file cannot be loaded or parsed."""


def _load_pyproject_version(path: Path = _DEFAULT_PYPROJECT) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _LoadError(f"cannot read pyproject.toml: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise _LoadError(f"cannot parse pyproject.toml: {exc}") from exc
    project = data.get("project") or {}
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise _LoadError("pyproject.toml: [project].version missing or non-string")
    return version


def _load_plugin_version(path: Path = _DEFAULT_PLUGIN_JSON) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _LoadError(f"cannot read plugin.json: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _LoadError(f"cannot parse plugin.json: {exc}") from exc
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str) or not version:
        raise _LoadError("plugin.json: top-level 'version' missing or non-string")
    return version


def _load_csharp_constants(path: Path = _DEFAULT_CSHARP) -> dict[str, object]:
    """Return ``{"bridge_version": str, "protocol_version": int, "severities": set[str]}``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _LoadError(f"cannot read C# bridge source: {exc}") from exc
    bridge_match = _RE_CSHARP_BRIDGE_VERSION.search(text)
    if not bridge_match:
        raise _LoadError("C# bridge source: BridgeVersion literal not found")
    proto_match = _RE_CSHARP_PROTOCOL_VERSION.search(text)
    if not proto_match:
        raise _LoadError("C# bridge source: ProtocolVersion literal not found")
    severities = {m.group(1) for m in _RE_CSHARP_SEVERITY.finditer(text)}
    if not severities:
        raise _LoadError("C# bridge source: no severity string literals found")
    return {
        "bridge_version": bridge_match.group(1),
        "protocol_version": int(proto_match.group(1)),
        "severities": severities,
    }


# ---------- Drift detection ------------------------------------------------


def _check_versions(py_version: str, plugin_version: str, cs_version: str) -> list[str]:
    """Return a list of drift messages naming 'version' (empty when aligned)."""
    values = {
        "pyproject": py_version,
        "plugin.json": plugin_version,
        "C# BridgeVersion": cs_version,
    }
    unique = set(values.values())
    if len(unique) == 1:
        return []
    fragments = ", ".join(f"{name}={value!r}" for name, value in values.items())
    return [f"version drift: {fragments}"]


def _check_protocol(py_protocol: int, cs_protocol: int) -> list[str]:
    """Return a list of drift messages naming 'protocol' (empty when aligned)."""
    if py_protocol == cs_protocol:
        return []
    return [
        f"protocol drift: Python PROTOCOL_VERSION={py_protocol} vs "
        f"C# ProtocolVersion={cs_protocol}"
    ]


def _check_severities(py_severities: set[str], cs_severities: set[str]) -> list[str]:
    """Return a list of drift messages naming 'severity' (empty when aligned).

    The canonical direction is: every severity emitted by the C# bridge must
    be present in Python's accepted vocabulary.  Python-only severities
    (e.g. ``critical``, which is used for runtime-validation-only signals)
    are permitted; C# emitting a severity Python does not know is drift.
    """
    extra = sorted(cs_severities - py_severities)
    if not extra:
        return []
    return [
        "severity drift: C# bridge emits severities not in Python "
        f"VALID_SEVERITIES: {extra}"
    ]


# ---------- Entry point ---------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Drift-check entry point.

    ``argv`` is accepted for parity with other script entry points but is
    currently ignored (no flags).  Returns 0 on clean, 1 on drift, 2 on
    input load failure.
    """
    del argv  # unused; kept for interface parity

    # Python-side constants (import lazily so _LoadError isn't mingled with
    # ImportError — a failed import here is a bug in the repo, not drift).
    from prefab_sentinel.bridge_constants import (
        PROTOCOL_VERSION as PY_PROTOCOL_VERSION,
        VALID_SEVERITIES as PY_VALID_SEVERITIES,
    )

    try:
        py_version = _load_pyproject_version()
        plugin_version = _load_plugin_version()
        cs = _load_csharp_constants()
    except _LoadError as exc:
        print(f"check_bridge_constants: input load failure: {exc}", file=sys.stderr)
        return 2

    drifts: list[str] = []
    drifts.extend(_check_versions(py_version, plugin_version, str(cs["bridge_version"])))
    drifts.extend(_check_protocol(int(PY_PROTOCOL_VERSION), int(cs["protocol_version"])))
    drifts.extend(_check_severities(set(PY_VALID_SEVERITIES), set(cs["severities"])))  # type: ignore[arg-type]

    if drifts:
        print("check_bridge_constants: drift detected", file=sys.stderr)
        for line in drifts:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("check_bridge_constants: all invariants aligned")
    return 0


if __name__ == "__main__":  # pragma: no cover — invoked from hook/CI
    sys.exit(main(sys.argv[1:]))
