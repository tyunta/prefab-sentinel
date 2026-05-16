from __future__ import annotations

import unittest

from prefab_sentinel.contracts import (
    Diagnostic,
    Severity,
    ToolResponse,
    _diagnostic_to_wire,
    error_response,
    max_severity,
    success_response,
)


class ContractTests(unittest.TestCase):
    def test_max_severity_picks_highest_level(self) -> None:
        result = max_severity([Severity.INFO, Severity.WARNING, Severity.CRITICAL])
        self.assertEqual(Severity.CRITICAL, result)

    def test_max_severity_defaults_to_info_for_empty(self) -> None:
        result = max_severity([])
        self.assertEqual(Severity.INFO, result)


class DiagnosticTests(unittest.TestCase):
    def test_construction(self) -> None:
        d = Diagnostic(path="/a.prefab", location="114:42", detail="broken_ref", evidence="guid abc not found")
        self.assertEqual(d.path, "/a.prefab")
        self.assertEqual(d.location, "114:42")
        self.assertEqual(d.detail, "broken_ref")
        self.assertEqual(d.evidence, "guid abc not found")


class ToolResponseTests(unittest.TestCase):
    def test_to_dict_serializes_severity_as_string(self) -> None:
        r = ToolResponse(success=True, severity=Severity.WARNING, code="T", message="m")
        d = r.to_dict()
        self.assertEqual(d["severity"], "warning")
        self.assertIs(d["success"], True)

    def test_defaults_for_data_and_diagnostics(self) -> None:
        r = ToolResponse(success=True, severity=Severity.INFO, code="T", message="m")
        self.assertEqual(r.data, {})
        self.assertEqual(r.diagnostics, [])

    def test_to_dict_includes_diagnostics(self) -> None:
        # Issue #304: every diagnostic on the envelope wire surfaces in
        # the four-key unified shape regardless of internal
        # construction path. The legacy field names move to the data
        # payload (path/location) or to message/code (detail/evidence).
        diag = Diagnostic(path="p", location="l", detail="d", evidence="e")
        r = ToolResponse(success=False, severity=Severity.ERROR, code="E", message="err", diagnostics=[diag])
        d = r.to_dict()
        self.assertEqual(len(d["diagnostics"]), 1)
        wire = d["diagnostics"][0]
        self.assertEqual(
            {"severity", "code", "message", "data"},
            set(wire.keys()),
        )
        self.assertEqual("d", wire["code"])
        self.assertEqual("e", wire["message"])
        self.assertEqual("p", wire["data"]["path"])
        self.assertEqual("l", wire["data"]["location"])


class ErrorResponseTests(unittest.TestCase):
    def test_defaults(self) -> None:
        r = error_response("ERR001", "something failed")
        self.assertFalse(r.success)
        self.assertEqual(r.severity, Severity.ERROR)
        self.assertEqual(r.code, "ERR001")
        self.assertEqual(r.message, "something failed")
        self.assertEqual(r.data, {})
        self.assertEqual(r.diagnostics, [])

    def test_severity_override(self) -> None:
        r = error_response("W001", "warning", severity=Severity.WARNING)
        self.assertFalse(r.success)
        self.assertEqual(r.severity, Severity.WARNING)

    def test_with_data_and_diagnostics(self) -> None:
        diag = Diagnostic(path="p", location="l", detail="d", evidence="e")
        r = error_response("E", "m", data={"k": "v"}, diagnostics=[diag])
        self.assertEqual(r.data, {"k": "v"})
        self.assertEqual(len(r.diagnostics), 1)

    def test_to_dict_roundtrip(self) -> None:
        r = error_response("E", "m", data={"x": 1})
        d = r.to_dict()
        self.assertEqual(d["success"], False)
        self.assertEqual(d["severity"], "error")
        self.assertEqual(d["data"]["x"], 1)


class SuccessResponseTests(unittest.TestCase):
    def test_defaults(self) -> None:
        r = success_response("OK", "done")
        self.assertTrue(r.success)
        self.assertEqual(r.severity, Severity.INFO)
        self.assertEqual(r.code, "OK")
        self.assertEqual(r.message, "done")
        self.assertEqual(r.data, {})
        self.assertEqual(r.diagnostics, [])

    def test_severity_override(self) -> None:
        r = success_response("OK", "done", severity=Severity.WARNING)
        self.assertTrue(r.success)
        self.assertEqual(r.severity, Severity.WARNING)

    def test_to_dict_roundtrip(self) -> None:
        r = success_response("OK", "done", data={"count": 5})
        d = r.to_dict()
        self.assertEqual(d["success"], True)
        self.assertEqual(d["severity"], "info")
        self.assertEqual(d["data"]["count"], 5)


class TestDiagnosticWireAdapter(unittest.TestCase):
    """Issue #304: ``_diagnostic_to_wire`` produces the four-key wire
    dict for a single diagnostic. The adapter is the single
    serialization boundary that every diagnostic crosses on the way
    out of the MCP server.
    """

    def test_adapter_copies_identifying_fields_into_wire_slots(self) -> None:
        diag = Diagnostic(
            path="Assets/Foo.prefab",
            location="114:42",
            detail="broken_ref",
            evidence="guid abc not found",
        )
        wire = _diagnostic_to_wire(diag, default_severity=Severity.ERROR)
        self.assertEqual(
            {"severity", "code", "message", "data"},
            set(wire.keys()),
        )
        self.assertEqual("error", wire["severity"])
        self.assertEqual("broken_ref", wire["code"])
        self.assertEqual("guid abc not found", wire["message"])
        self.assertEqual("Assets/Foo.prefab", wire["data"]["path"])
        self.assertEqual("114:42", wire["data"]["location"])

    def test_adapter_falls_back_to_category_when_evidence_is_empty(self) -> None:
        diag = Diagnostic(path="p", location="l", detail="missing_asset", evidence="")
        wire = _diagnostic_to_wire(diag, default_severity=Severity.INFO)
        self.assertEqual("info", wire["severity"])
        self.assertEqual("missing_asset", wire["code"])
        # Empty evidence falls back to the category so consumers
        # always observe a non-empty message field.
        self.assertEqual("missing_asset", wire["message"])

    def test_adapter_omits_empty_locator_keys_from_data_payload(self) -> None:
        diag = Diagnostic(path="", location="", detail="schema_error", evidence="bad")
        wire = _diagnostic_to_wire(diag, default_severity=Severity.WARNING)
        self.assertEqual("warning", wire["severity"])
        self.assertEqual({}, wire["data"])
        # Misleading empty-string locator keys must not appear on the
        # wire; consumers branch on key presence, not on truthiness.
        self.assertNotIn("path", wire["data"])
        self.assertNotIn("location", wire["data"])


class TestToolResponseWireShape(unittest.TestCase):
    """Issue #304: the envelope serializes every diagnostic in the
    unified four-key wire shape regardless of internal construction
    path. Legacy keys (``path`` / ``location`` / ``detail`` /
    ``evidence``) do not appear on the diagnostic entries on the wire.
    """

    def test_envelope_diagnostics_carry_only_four_documented_keys(self) -> None:
        diag = Diagnostic(path="p", location="l", detail="d", evidence="e")
        envelope = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="E",
            message="err",
            diagnostics=[diag],
        )
        wire = envelope.to_dict()
        self.assertEqual(1, len(wire["diagnostics"]))
        entry = wire["diagnostics"][0]
        self.assertEqual(
            {"severity", "code", "message", "data"},
            set(entry.keys()),
            f"legacy diagnostic keys leaked onto the wire: {entry!r}",
        )


class SeverityOrderTests(unittest.TestCase):
    def test_ordering_info_lt_warning(self) -> None:
        self.assertEqual(max_severity([Severity.INFO, Severity.WARNING]), Severity.WARNING)

    def test_ordering_warning_lt_error(self) -> None:
        self.assertEqual(max_severity([Severity.WARNING, Severity.ERROR]), Severity.ERROR)

    def test_ordering_error_lt_critical(self) -> None:
        self.assertEqual(max_severity([Severity.ERROR, Severity.CRITICAL]), Severity.CRITICAL)

    def test_single_element(self) -> None:
        self.assertEqual(max_severity([Severity.WARNING]), Severity.WARNING)


if __name__ == "__main__":
    unittest.main()
