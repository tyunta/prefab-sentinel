from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from prefab_sentinel.contracts import Severity, ToolResponse
from tests._typing_helpers import (
    load_json_list,
    load_json_object,
    require_list,
    require_magic_mock,
    require_mapping,
    require_not_none,
    require_str,
    require_tool_response,
)


class TypingHelperMappingTests(unittest.TestCase):
    def test_mapping_helper_preserves_observed_payload(self) -> None:
        payload = {"data": 1}

        narrowed = require_mapping(payload, "payload")

        self.assertEqual({"data": 1}, narrowed)

    def test_mapping_helper_rejects_non_dict_with_label_and_type(self) -> None:
        with self.assertRaises(AssertionError) as cm:
            require_mapping([1], "payload")

        message = str(cm.exception)
        self.assertIn("payload", message)
        self.assertIn("list", message)


class TypingHelperListTests(unittest.TestCase):
    def test_list_helper_preserves_list_and_rejects_other_roots(self) -> None:
        self.assertEqual([1], require_list([1], "items"))

        with self.assertRaises(AssertionError) as cm:
            require_list({"data": 1}, "items")

        message = str(cm.exception)
        self.assertIn("items", message)
        self.assertIn("dict", message)


class TypingHelperStringTests(unittest.TestCase):
    def test_string_helper_preserves_text_and_rejects_other_values(self) -> None:
        self.assertEqual("abc", require_str("abc", "name"))

        with self.assertRaises(AssertionError) as cm:
            require_str(3, "name")

        message = str(cm.exception)
        self.assertIn("name", message)
        self.assertIn("int", message)


class TypingHelperOptionalTests(unittest.TestCase):
    def test_non_none_helper_returns_present_value_and_rejects_none(self) -> None:
        self.assertEqual("match", require_not_none("match", "regex"))

        with self.assertRaises(AssertionError) as cm:
            require_not_none(None, "regex")

        self.assertIn("regex", str(cm.exception))


class TypingHelperJsonTests(unittest.TestCase):
    def test_json_object_helper_returns_dict_and_rejects_wrong_roots(self) -> None:
        self.assertEqual({"ok": True}, load_json_object('{"ok": true}', "wire"))

        with self.assertRaises(AssertionError) as wrong_root:
            load_json_object("[1]", "wire")

        wrong_root_message = str(wrong_root.exception)
        self.assertIn("wire", wrong_root_message)
        self.assertIn("list", wrong_root_message)

        with self.assertRaises(json.JSONDecodeError) as malformed:
            load_json_object("{", "wire")

        self.assertIn("Expecting property name", str(malformed.exception))

    def test_json_list_helper_returns_list_and_rejects_wrong_roots(self) -> None:
        self.assertEqual([{"ok": True}], load_json_list('[{"ok": true}]', "shapes"))

        with self.assertRaises(AssertionError) as wrong_root:
            load_json_list('{"ok": true}', "shapes")

        wrong_root_message = str(wrong_root.exception)
        self.assertIn("shapes", wrong_root_message)
        self.assertIn("dict", wrong_root_message)

        with self.assertRaises(json.JSONDecodeError) as malformed:
            load_json_list("[", "shapes")

        self.assertIn("Expecting value", str(malformed.exception))


class TypingHelperToolResponseTests(unittest.TestCase):
    def test_tool_response_helper_preserves_response_and_rejects_other_objects(self) -> None:
        response = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="OK",
            message="ok",
            data={"value": 1},
        )

        narrowed = require_tool_response(response, "parse")

        self.assertEqual(("OK", {"value": 1}), (narrowed.code, narrowed.data))
        with self.assertRaises(AssertionError) as cm:
            require_tool_response({"code": "OK"}, "parse")

        message = str(cm.exception)
        self.assertIn("parse", message)
        self.assertIn("dict", message)


class TypingHelperMagicMockTests(unittest.TestCase):
    def test_magic_mock_helper_preserves_mock_api_and_rejects_plain_objects(self) -> None:
        mock = MagicMock()
        mock.return_value = "sentinel"

        narrowed = require_magic_mock(mock, "send")

        self.assertEqual("sentinel", narrowed.return_value)
        narrowed.assert_not_called()
        with self.assertRaises(AssertionError) as cm:
            require_magic_mock(object(), "send")

        self.assertIn("send", str(cm.exception))
