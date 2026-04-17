"""Shared constants for Unity bridge communication.

Environment variable names, severity sets, and supported file suffixes
used across bridge_smoke, editor_bridge, integration_tests, and
tools/unity_patch_bridge.
"""

from __future__ import annotations

# Environment variable names for Unity bridge configuration
UNITY_COMMAND_ENV = "UNITYTOOL_UNITY_COMMAND"
UNITY_PROJECT_PATH_ENV = "UNITYTOOL_UNITY_PROJECT_PATH"
UNITY_EXECUTE_METHOD_ENV = "UNITYTOOL_UNITY_EXECUTE_METHOD"
UNITY_TIMEOUT_SEC_ENV = "UNITYTOOL_UNITY_TIMEOUT_SEC"
UNITY_LOG_FILE_ENV = "UNITYTOOL_UNITY_LOG_FILE"
BRIDGE_MODE_ENV = "UNITYTOOL_BRIDGE_MODE"
BRIDGE_WATCH_DIR_ENV = "UNITYTOOL_BRIDGE_WATCH_DIR"

# Bridge wire protocol version — must match ``ProtocolVersion`` in
# ``tools/unity/PrefabSentinel.UnityEditorControlBridge.cs``.  Drift between
# the two is surfaced by ``scripts/check_bridge_constants.py``.
PROTOCOL_VERSION = 1

# Valid severity levels for bridge responses
VALID_SEVERITIES = frozenset({"info", "warning", "error", "critical"})
