from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from unitytool import cli

MISSING_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class CliTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = cli.main(argv)
        return exit_code, buf.getvalue()

    def test_inspect_variant_fail_fast_on_missing_variant(self) -> None:
        exit_code, output = self.run_cli(
            ["inspect", "variant", "--path", "Assets/Test Variant.prefab"]
        )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("INSPECT_VARIANT_RESULT", payload["code"])
        self.assertEqual("error", payload["severity"])
        self.assertTrue(payload["data"]["read_only"])
        self.assertTrue(payload["data"]["fail_fast_triggered"])
        self.assertEqual("PVR404", payload["data"]["steps"][0]["result"]["code"])

    def test_validate_refs_returns_missing_scope_error(self) -> None:
        exit_code, output = self.run_cli(
            ["validate", "refs", "--scope", "Assets/haiirokoubou"]
        )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("VALIDATE_REFS_RESULT", payload["code"])
        self.assertEqual("error", payload["severity"])
        self.assertTrue(payload["data"]["read_only"])
        self.assertEqual("REF404", payload["data"]["steps"][0]["result"]["code"])
        self.assertEqual([], payload["diagnostics"])

    def test_inspect_where_used_returns_missing_scope_error(self) -> None:
        exit_code, output = self.run_cli(
            [
                "inspect",
                "where-used",
                "--asset-or-guid",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "--scope",
                "Assets/haiirokoubou",
            ]
        )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("INSPECT_WHERE_USED_RESULT", payload["code"])
        self.assertEqual("error", payload["severity"])
        self.assertEqual("REF404", payload["data"]["steps"][0]["result"]["code"])

    def test_suggest_ignore_guids_returns_missing_scope_error(self) -> None:
        exit_code, output = self.run_cli(
            ["suggest", "ignore-guids", "--scope", "Assets/haiirokoubou"]
        )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("SUGGEST_IGNORE_GUIDS_RESULT", payload["code"])
        self.assertEqual("error", payload["severity"])
        self.assertTrue(payload["data"]["read_only"])
        self.assertEqual("REF404", payload["data"]["steps"][0]["result"]["code"])

    def test_validate_refs_loads_ignore_guid_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ignore_guids.txt"
            path.write_text(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n# comment\nbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
                encoding="utf-8",
            )
            exit_code, output = self.run_cli(
                [
                    "validate",
                    "refs",
                    "--scope",
                    "Assets/haiirokoubou",
                    "--ignore-guid-file",
                    str(path),
                ]
            )
        payload = json.loads(output)
        self.assertEqual(0, exit_code)
        self.assertEqual("VALIDATE_REFS_RESULT", payload["code"])
        self.assertEqual("REF404", payload["data"]["steps"][0]["result"]["code"])

    def test_suggest_ignore_guids_writes_ignore_guid_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write(
                root / "Assets" / "Ref.prefab",
                f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 11400000, guid: {MISSING_GUID}, type: 3}}
""",
            )
            out_file = root / "config" / "ignore_guids.txt"

            exit_code, output = self.run_cli(
                [
                    "suggest",
                    "ignore-guids",
                    "--scope",
                    str(root / "Assets"),
                    "--min-occurrences",
                    "1",
                    "--max-items",
                    "10",
                    "--out-ignore-guid-file",
                    str(out_file),
                    "--out-ignore-guid-mode",
                    "replace",
                ]
            )

            payload = json.loads(output)
            self.assertEqual(0, exit_code)
            self.assertEqual("SUGGEST_IGNORE_GUIDS_RESULT", payload["code"])
            self.assertTrue(out_file.exists())
            lines = out_file.read_text(encoding="utf-8").splitlines()
            self.assertIn(MISSING_GUID, lines)
            self.assertEqual(1, payload["data"]["ignore_file_update"]["added"])

            exit_code, output = self.run_cli(
                [
                    "suggest",
                    "ignore-guids",
                    "--scope",
                    str(root / "Assets"),
                    "--min-occurrences",
                    "1",
                    "--max-items",
                    "10",
                    "--out-ignore-guid-file",
                    str(out_file),
                    "--out-ignore-guid-mode",
                    "append",
                ]
            )

            payload = json.loads(output)
            self.assertEqual(0, exit_code)
            self.assertEqual(0, payload["data"]["ignore_file_update"]["added"])

    def test_report_export_writes_markdown(self) -> None:
        payload = {
            "success": False,
            "severity": "warning",
            "code": "PHASE1_STUB",
            "message": "stub",
            "data": {"scope": "Assets/demo"},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            src = temp / "input.json"
            dst = temp / "out.md"
            src.write_text(json.dumps(payload), encoding="utf-8")

            exit_code, output = self.run_cli(
                [
                    "report",
                    "export",
                    "--input",
                    str(src),
                    "--format",
                    "md",
                    "--out",
                    str(dst),
                ]
            )

            self.assertEqual(0, exit_code)
            self.assertIn("Exported report:", output)
            self.assertTrue(dst.exists())
            content = dst.read_text(encoding="utf-8")
            self.assertIn("# UnityTool Validation Report", content)

    def test_report_export_markdown_limits_usages(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "INSPECT_WHERE_USED_RESULT",
            "message": "ok",
            "data": {
                "steps": [
                    {
                        "step": "where_used",
                        "result": {
                            "data": {
                                "usages": [
                                    {"path": "A"},
                                    {"path": "B"},
                                ]
                            }
                        },
                    }
                ]
            },
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            src = temp / "input.json"
            dst = temp / "out.md"
            src.write_text(json.dumps(payload), encoding="utf-8")

            exit_code, _ = self.run_cli(
                [
                    "report",
                    "export",
                    "--input",
                    str(src),
                    "--format",
                    "md",
                    "--out",
                    str(dst),
                    "--md-max-usages",
                    "1",
                ]
            )

            self.assertEqual(0, exit_code)
            content = dst.read_text(encoding="utf-8")
            self.assertIn('"usages_total": 2', content)
            self.assertIn('"usages_truncated_for_markdown": 1', content)

    def test_report_export_markdown_omit_usages(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "INSPECT_WHERE_USED_RESULT",
            "message": "ok",
            "data": {"usages": [{"path": "A"}]},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            src = temp / "input.json"
            dst = temp / "out.md"
            src.write_text(json.dumps(payload), encoding="utf-8")

            exit_code, _ = self.run_cli(
                [
                    "report",
                    "export",
                    "--input",
                    str(src),
                    "--format",
                    "md",
                    "--out",
                    str(dst),
                    "--md-omit-usages",
                ]
            )

            self.assertEqual(0, exit_code)
            content = dst.read_text(encoding="utf-8")
            self.assertIn('"usages": []', content)


if __name__ == "__main__":
    unittest.main()
