"""Tests for prefab_sentinel.json_io module."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from prefab_sentinel.json_io import dump_json, load_json, load_json_file


class TestDumpJson(unittest.TestCase):
    """dump_json serializes with ensure_ascii=False and indent=2 by default."""

    def test_defaults(self) -> None:
        result = dump_json({"a": 1})
        self.assertEqual(result, '{\n  "a": 1\n}')

    def test_non_ascii_compact(self) -> None:
        result = dump_json({"a": "あ"}, indent=None)
        self.assertEqual(result, '{"a": "あ"}')

    def test_override_ensure_ascii(self) -> None:
        result = dump_json({"a": "あ"}, ensure_ascii=True)
        parsed = json.loads(result)
        self.assertEqual(parsed, {"a": "あ"})
        self.assertNotIn("あ", result)

    def test_non_serializable_raises_type_error(self) -> None:
        with self.assertRaises(TypeError) as cm:
            dump_json(object())
        self.assertIn("not JSON serializable", str(cm.exception))


class TestLoadJson(unittest.TestCase):
    """load_json deserializes JSON strings."""

    def test_valid_json(self) -> None:
        result = load_json('{"a": 1}')
        self.assertEqual(result, {"a": 1})

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            load_json("invalid")


class TestLoadJsonFile(unittest.TestCase):
    """load_json_file reads and parses JSON files."""

    def test_valid_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write('{"x": 1}')
            f.flush()
            self.addCleanup(os.unlink, f.name)
            result = load_json_file(f.name)
        self.assertEqual(result, {"x": 1})

    def test_missing_file_raises_os_error(self) -> None:
        with self.assertRaises(OSError):
            load_json_file("/nonexistent/path/to/file.json")

    def test_bad_json_raises(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{broken")
            f.flush()
            self.addCleanup(os.unlink, f.name)
            with self.assertRaises(json.JSONDecodeError):
                load_json_file(f.name)


if __name__ == "__main__":
    unittest.main()
