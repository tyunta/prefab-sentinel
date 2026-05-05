from __future__ import annotations

import unittest
from typing import Any

from prefab_sentinel.bridge_smoke import (
    apply_applied_expectation,
    apply_code_expectation,
    build_bridge_env,
    extract_applied_count,
    resolve_expected_applied,
    validate_bridge_response,
    validate_expectation,
)


def _valid_response(
    *,
    success: bool = True,
    severity: str = "info",
    code: str = "OK",
    message: str = "ok",
    data: dict[str, Any] | None = None,
    diagnostics: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "severity": severity,
        "code": code,
        "message": message,
        "data": data if data is not None else {},
        "diagnostics": diagnostics if diagnostics is not None else [],
    }


class ValidateBridgeResponseTests(unittest.TestCase):
    def test_valid_response_passes(self) -> None:
        validate_bridge_response(_valid_response())

    def test_missing_field_raises(self) -> None:
        for field in ("success", "severity", "code", "message", "data", "diagnostics"):
            with self.subTest(field=field):
                resp = _valid_response()
                del resp[field]
                with self.assertRaises(RuntimeError) as cm:
                    validate_bridge_response(resp)
                self.assertIn(field, str(cm.exception))

    def test_non_bool_success_raises(self) -> None:
        with self.assertRaises(RuntimeError) as cm:
            validate_bridge_response(_valid_response(success="yes"))  # type: ignore[arg-type]
        self.assertIn("success", str(cm.exception))

    def test_invalid_severity_raises(self) -> None:
        with self.assertRaises(RuntimeError) as cm:
            validate_bridge_response(_valid_response(severity="fatal"))
        self.assertIn("severity", str(cm.exception))

    def test_all_valid_severities(self) -> None:
        for sev in ("info", "warning", "error", "critical"):
            with self.subTest(severity=sev):
                validate_bridge_response(_valid_response(severity=sev))

    def test_empty_code_raises(self) -> None:
        with self.assertRaises(RuntimeError) as cm:
            validate_bridge_response(_valid_response(code="  "))
        self.assertIn("code", str(cm.exception))

    def test_non_string_code_raises(self) -> None:
        resp = _valid_response()
        resp["code"] = 123
        with self.assertRaises(RuntimeError) as cm:
            validate_bridge_response(resp)
        self.assertIn("code", str(cm.exception))

    def test_non_string_message_raises(self) -> None:
        resp = _valid_response()
        resp["message"] = 123
        with self.assertRaises(RuntimeError) as cm:
            validate_bridge_response(resp)
        self.assertIn("message", str(cm.exception))

    def test_non_dict_data_raises(self) -> None:
        resp = _valid_response()
        resp["data"] = "not_a_dict"
        with self.assertRaises(RuntimeError) as cm:
            validate_bridge_response(resp)
        self.assertIn("data", str(cm.exception))

    def test_non_list_diagnostics_raises(self) -> None:
        resp = _valid_response()
        resp["diagnostics"] = "not_a_list"
        with self.assertRaises(RuntimeError) as cm:
            validate_bridge_response(resp)
        self.assertIn("diagnostics", str(cm.exception))


class ResolveExpectedAppliedTests(unittest.TestCase):
    def test_explicit_value(self) -> None:
        result, source = resolve_expected_applied(
            plan={"ops": [1, 2]}, expected_applied=5, expect_applied_from_plan=True, expect_failure=False
        )
        self.assertEqual(result, 5)
        self.assertEqual(source, "cli")

    def test_no_plan_inference(self) -> None:
        result, source = resolve_expected_applied(
            plan={"ops": [1, 2]}, expected_applied=None, expect_applied_from_plan=False, expect_failure=False
        )
        self.assertIsNone(result)
        self.assertEqual(source, "none")

    def test_expect_failure_skips(self) -> None:
        result, source = resolve_expected_applied(
            plan={"ops": [1, 2]}, expected_applied=None, expect_applied_from_plan=True, expect_failure=True
        )
        self.assertIsNone(result)
        self.assertEqual(source, "skipped_expect_failure")

    def test_from_plan_ops(self) -> None:
        result, source = resolve_expected_applied(
            plan={"ops": [1, 2, 3]}, expected_applied=None, expect_applied_from_plan=True, expect_failure=False
        )
        self.assertEqual(result, 3)
        self.assertEqual(source, "plan_ops")


class BuildBridgeEnvTests(unittest.TestCase):
    def test_base_env_used(self) -> None:
        env = build_bridge_env(base_env={"EXISTING": "value"})
        self.assertEqual(env["EXISTING"], "value")

    def test_override_applied(self) -> None:
        env = build_bridge_env(
            base_env={},
            unity_command="/usr/bin/unity",
            unity_project_path="/project",
            unity_execute_method="Foo.Bar",
            unity_timeout_sec=300,
            unity_log_file="/tmp/log.txt",
        )
        self.assertEqual(env["UNITYTOOL_UNITY_COMMAND"], "/usr/bin/unity")
        self.assertEqual(env["UNITYTOOL_UNITY_PROJECT_PATH"], "/project")
        self.assertEqual(env["UNITYTOOL_UNITY_EXECUTE_METHOD"], "Foo.Bar")
        self.assertEqual(env["UNITYTOOL_UNITY_TIMEOUT_SEC"], "300")
        self.assertEqual(env["UNITYTOOL_UNITY_LOG_FILE"], "/tmp/log.txt")

    def test_none_values_skipped(self) -> None:
        env = build_bridge_env(base_env={"A": "1"}, unity_command=None)
        self.assertNotIn("UNITYTOOL_UNITY_COMMAND", env)
        self.assertEqual(env["A"], "1")

    def test_default_base_env_copies_os_environ(self) -> None:
        env = build_bridge_env()
        self.assertIsInstance(env, dict)


class ExtractAppliedCountTests(unittest.TestCase):
    def test_valid_int(self) -> None:
        self.assertEqual(extract_applied_count({"data": {"applied": 5}}), 5)

    def test_bool_returns_none(self) -> None:
        self.assertIsNone(extract_applied_count({"data": {"applied": True}}))

    def test_missing_applied(self) -> None:
        self.assertIsNone(extract_applied_count({"data": {}}))

    def test_non_dict_data(self) -> None:
        self.assertIsNone(extract_applied_count({"data": "not_dict"}))

    def test_missing_data(self) -> None:
        self.assertIsNone(extract_applied_count({}))

    def test_string_applied_returns_none(self) -> None:
        self.assertIsNone(extract_applied_count({"data": {"applied": "3"}}))

    def test_zero_applied(self) -> None:
        self.assertEqual(extract_applied_count({"data": {"applied": 0}}), 0)


class ApplyAppliedExpectationTests(unittest.TestCase):
    def test_none_expected_returns_none(self) -> None:
        resp = _valid_response(data={"applied": 5})
        self.assertIsNone(apply_applied_expectation(resp, None))

    def test_match(self) -> None:
        resp = _valid_response(data={"applied": 3})
        result = apply_applied_expectation(resp, 3, "cli")
        self.assertTrue(result)
        self.assertEqual(resp["data"]["expected_applied"], 3)
        self.assertEqual(resp["data"]["actual_applied"], 3)
        self.assertTrue(resp["data"]["applied_matches"])
        self.assertEqual(resp["data"]["expected_applied_source"], "cli")

    def test_mismatch(self) -> None:
        resp = _valid_response(data={"applied": 2})
        result = apply_applied_expectation(resp, 5)
        self.assertFalse(result)
        self.assertFalse(resp["data"]["applied_matches"])

    def test_non_dict_data_returns_none(self) -> None:
        resp = _valid_response()
        resp["data"] = "not_dict"
        self.assertIsNone(apply_applied_expectation(resp, 3))

    def test_default_source_is_cli(self) -> None:
        resp = _valid_response(data={"applied": 1})
        apply_applied_expectation(resp, 1)
        self.assertEqual(resp["data"]["expected_applied_source"], "cli")


class ApplyCodeExpectationTests(unittest.TestCase):
    def test_none_expected_returns_none(self) -> None:
        resp = _valid_response()
        self.assertIsNone(apply_code_expectation(resp, None))

    def test_match(self) -> None:
        resp = _valid_response(code="BRIDGE_OK")
        result = apply_code_expectation(resp, "BRIDGE_OK")
        self.assertTrue(result)
        self.assertTrue(resp["data"]["code_matches"])
        self.assertEqual(resp["data"]["expected_code"], "BRIDGE_OK")
        self.assertEqual(resp["data"]["actual_code"], "BRIDGE_OK")

    def test_mismatch(self) -> None:
        resp = _valid_response(code="BRIDGE_OK")
        result = apply_code_expectation(resp, "BRIDGE_FAIL")
        self.assertFalse(result)
        self.assertFalse(resp["data"]["code_matches"])

    def test_non_dict_data_returns_none(self) -> None:
        resp = _valid_response()
        resp["data"] = "not_dict"
        self.assertIsNone(apply_code_expectation(resp, "OK"))

    def test_non_string_code_gives_none_actual(self) -> None:
        resp = _valid_response()
        resp["code"] = 123
        result = apply_code_expectation(resp, "OK")
        self.assertFalse(result)
        self.assertIsNone(resp["data"]["actual_code"])


class ValidateExpectationTests(unittest.TestCase):
    def test_success_expected_success(self) -> None:
        resp = _valid_response(success=True)
        self.assertTrue(validate_expectation(resp, expect_failure=False))

    def test_failure_expected_failure(self) -> None:
        resp = _valid_response(success=False, severity="error")
        self.assertTrue(validate_expectation(resp, expect_failure=True))

    def test_success_expected_failure(self) -> None:
        resp = _valid_response(success=True)
        self.assertFalse(validate_expectation(resp, expect_failure=True))

    def test_failure_expected_success(self) -> None:
        resp = _valid_response(success=False, severity="error")
        self.assertFalse(validate_expectation(resp, expect_failure=False))

    def test_code_mismatch_overrides_success(self) -> None:
        resp = _valid_response(success=True, code="WRONG")
        result = validate_expectation(resp, expect_failure=False, expected_code="EXPECTED")
        self.assertFalse(result)

    def test_applied_mismatch_overrides_success(self) -> None:
        resp = _valid_response(success=True, data={"applied": 1})
        result = validate_expectation(resp, expect_failure=False, expected_applied=99)
        self.assertFalse(result)

    def test_code_and_applied_match(self) -> None:
        resp = _valid_response(success=True, code="OK", data={"applied": 3})
        result = validate_expectation(
            resp, expect_failure=False, expected_code="OK", expected_applied=3, expected_applied_source="cli"
        )
        self.assertTrue(result)

    def test_none_applied_does_not_block(self) -> None:
        resp = _valid_response(success=True)
        result = validate_expectation(resp, expect_failure=False, expected_applied=None)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
