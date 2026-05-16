"""Wire-level bridge code constants for ``unity_patch_bridge`` (issue #297).

The patch bridge driver previously interpolated wire-code strings as
bare literals at 23 call sites; a rename or typo would surface only at
client-side log inspection. The constants below give each wire code a
named binding so static analysis and grep equally reveal every emit
site, and so a future code rename surfaces as a Python NameError at
import time rather than as silent client-side wire drift.

Each constant's value equals the existing wire string verbatim — the
module is a pure inventory and has no behaviour beyond declaration.
"""

from __future__ import annotations

# Protocol-version mismatch: the bridge received a request whose
# ``protocol_version`` field does not match the bridge's own
# ``PROTOCOL_VERSION``.
BRIDGE_PROTOCOL_VERSION = "BRIDGE_PROTOCOL_VERSION"

# Unity-side response failed schema validation (missing/typed-wrong
# top-level field on the response envelope received from the Editor).
BRIDGE_UNITY_RESPONSE_SCHEMA = "BRIDGE_UNITY_RESPONSE_SCHEMA"

# Writing the request file to the editor watch directory failed
# (filesystem error before the Editor sees the request).
BRIDGE_EDITOR_WRITE = "BRIDGE_EDITOR_WRITE"

# Reading the response file produced by the Editor failed (filesystem
# or decode error after the Editor wrote a response file).
BRIDGE_EDITOR_RESPONSE_READ = "BRIDGE_EDITOR_RESPONSE_READ"

# The bridge polled the watch directory for a response file but no
# response appeared before the timeout (the Editor is unresponsive or
# the watch loop is wedged).
BRIDGE_EDITOR_TIMEOUT = "BRIDGE_EDITOR_TIMEOUT"

# Caller-supplied request payload failed top-level schema validation
# (missing/typed-wrong required field on the request envelope).
BRIDGE_REQUEST_SCHEMA = "BRIDGE_REQUEST_SCHEMA"

# Caller-supplied request payload was empty stdin (zero bytes).
BRIDGE_REQUEST_EMPTY = "BRIDGE_REQUEST_EMPTY"

# Caller-supplied request payload was non-empty but malformed JSON.
BRIDGE_REQUEST_JSON = "BRIDGE_REQUEST_JSON"

# Caller submitted a legacy request shape that the modern bridge
# refuses (issue #157 retired the legacy invocation form).
BRIDGE_LEGACY_SCHEMA_REJECTED = "BRIDGE_LEGACY_SCHEMA_REJECTED"

# Caller-supplied ``target`` resolves to a file suffix the bridge does
# not support (e.g. ``.cs`` instead of ``.prefab`` / ``.unity`` / …).
BRIDGE_UNSUPPORTED_TARGET = "BRIDGE_UNSUPPORTED_TARGET"

# Caller supplied a ``UNITYTOOL_UNITY_TIMEOUT_SEC`` env value that did
# not parse as a positive integer.
BRIDGE_TIMEOUT_INVALID = "BRIDGE_TIMEOUT_INVALID"

# ``UNITYTOOL_BRIDGE_WATCH_DIR`` is unset or names a directory that
# does not exist on disk (Editor Bridge is required).
BRIDGE_WATCH_DIR_MISSING = "BRIDGE_WATCH_DIR_MISSING"
