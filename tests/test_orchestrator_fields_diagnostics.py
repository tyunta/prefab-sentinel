"""Diagnostic-surfacing tests for orchestrator_fields entry points.

The rename-validation entry point performs a local source-text read on the
resolved script path as part of its derived-class lookup. When that read
fails, the response must surface a single ``unreadable_file`` diagnostic
and the rename impact for the primary script must still be reported with
an empty derived-class section.

The field-coverage entry point allocates a diagnostic sink and forwards it
to the project-wide scan helpers (``build_field_map``,
``build_class_name_index``, ``resolve_inherited_fields``); decode failures
in any of those scans must surface on the response.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.orchestrator_fields import (
    check_field_coverage,
    validate_field_rename,
)
from prefab_sentinel.services.reference_resolver import ReferenceResolverService


def _write_script(root: Path, rel_path: str, guid: str, source: str) -> Path:
    cs_path = root / rel_path
    cs_path.parent.mkdir(parents=True, exist_ok=True)
    cs_path.write_text(source, encoding="utf-8")
    meta = Path(str(cs_path) + ".meta")
    meta.write_text(
        f"fileFormatVersion: 2\nguid: {guid}\n",
        encoding="utf-8",
    )
    return cs_path


class TestValidateFieldRenameLocalReadDiagnostics(unittest.TestCase):
    """Local script-source read failure inside validate_field_rename."""

    def test_decode_failure_surfaces_diagnostic_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Assets").mkdir(parents=True, exist_ok=True)
            cs_path = _write_script(
                root,
                "Assets/Scripts/Player.cs",
                "11220000000000000000000000000099",
                "public class Player : MonoBehaviour {\n"
                "    public float speed;\n"
                "}\n",
            )
            resolver = ReferenceResolverService(root)

            real_read_text = Path.read_text
            call_counter = {"hits_on_target": 0}

            def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
                # Let resolve_script_fields' first read succeed; fail the
                # second local read inside validate_field_rename used for the
                # derived-class lookup.
                if self == cs_path:
                    call_counter["hits_on_target"] += 1
                    if call_counter["hits_on_target"] >= 2:
                        raise UnicodeDecodeError(
                            "utf-8", b"\xff", 0, 1, "simulated decode failure"
                        )
                return real_read_text(self, *args, **kwargs)

            with patch.object(Path, "read_text", fake_read_text):
                response = validate_field_rename(
                    resolver, str(cs_path), "speed", "moveSpeed"
                )

        self.assertTrue(response.success)
        diag_details = [d.detail for d in response.diagnostics]
        self.assertIn("unreadable_file", diag_details)
        # Primary-script rename impact still reported; derived-class section empty.
        self.assertEqual(0, response.data["derived_guids_scanned"])


class TestCheckFieldCoverageDiagnostics(unittest.TestCase):
    """Project-wide scans inside check_field_coverage surface decode failures."""

    def test_undecodable_cs_file_surfaces_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Assets").mkdir(parents=True, exist_ok=True)
            # Valid .cs file with companion meta — picked up by the GUID
            # index but harmless to the scan.
            _write_script(
                root,
                "Assets/Scripts/Player.cs",
                "11220000000000000000000000000099",
                "public class Player : MonoBehaviour {\n"
                "    public float speed;\n"
                "}\n",
            )
            # Binary .cs file with companion meta — fails UTF-8 decode and
            # records exactly one diagnostic.
            bad_path = root / "Assets/Scripts/Bad.cs"
            bad_path.write_bytes(b"\xff\xfe\x00\xff non-utf8 bytes \xc3\x28")
            (root / "Assets/Scripts/Bad.cs.meta").write_text(
                "fileFormatVersion: 2\n"
                "guid: 33440000000000000000000000000088\n",
                encoding="utf-8",
            )
            resolver = ReferenceResolverService(root)
            response = check_field_coverage(resolver, "Assets")

        self.assertTrue(response.success)
        diag_details = [d.detail for d in response.diagnostics]
        self.assertIn("unreadable_file", diag_details)


if __name__ == "__main__":
    unittest.main()
