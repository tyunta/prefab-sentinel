"""Shared constants for Unity Editor Bridge communication.

Environment variable names, the bridge protocol version, the severity
allow-list, and the console-log buffer cap consumed by the editor-bridge
client, the patch CLI, and the runtime validation invocation helper.
"""

from __future__ import annotations

# Environment variable names for Unity Editor Bridge configuration.
# ``UNITYTOOL_BRIDGE_WATCH_DIR`` is the sole connection knob; the
# remaining names cover ancillary configuration (project path lookup,
# log destination, poll-timeout override).
UNITY_PROJECT_PATH_ENV = "UNITYTOOL_UNITY_PROJECT_PATH"
UNITY_TIMEOUT_SEC_ENV = "UNITYTOOL_UNITY_TIMEOUT_SEC"
UNITY_LOG_FILE_ENV = "UNITYTOOL_UNITY_LOG_FILE"
BRIDGE_WATCH_DIR_ENV = "UNITYTOOL_BRIDGE_WATCH_DIR"

# Bridge wire protocol version — must match ``ProtocolVersion`` in
# ``tools/unity/PrefabSentinel.UnityEditorControlBridge.cs``.  Drift between
# the two is surfaced by ``scripts/check_bridge_constants.py``.
PROTOCOL_VERSION = 1

# Valid severity levels for bridge responses
VALID_SEVERITIES = frozenset({"info", "warning", "error", "critical"})

# Issue #131: Python mirror of the C# ``ConsoleLogBuffer.DefaultCapacity``
# constant.  The ``capture_console_logs`` request validator (Python and C#)
# rejects ``max_entries`` outside ``[1, CONSOLE_LOG_BUFFER_MAX_ENTRIES]``;
# the upper bound is the buffer capacity because the bridge can never
# return more entries than have been buffered.  Drift is caught by
# ``scripts/check_bridge_constants.py``.
CONSOLE_LOG_BUFFER_MAX_ENTRIES = 1000
