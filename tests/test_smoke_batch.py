from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from prefab_sentinel.smoke_batch import (
    SmokeCase,
    _build_smoke_command,
    _extract_applied_count,
    _load_timeout_profile_map,
    _parse_case_payload,
    _render_markdown_summary,
    _resolve_case_unity_timeout_sec,
    _resolve_targets,
)


def _make_case(
    *,
    name: str = "avatar",
    plan: str = "/tmp/plan.json",
    project_path: str = "/tmp/project",
    expect_failure: bool = False,
    expected_code: str | None = None,
    expected_applied: int | None = None,
) -> SmokeCase:
    return SmokeCase(
        name=name,
        plan=Path(plan),
        project_path=Path(project_path),
        expect_failure=expect_failure,
        expected_code=expected_code,
        expected_applied=expected_applied,
    )


class ResolveTargetsTests(unittest.TestCase):
    def test_all_expands(self) -> None:
        self.assertEqual(_resolve_targets(["all"]), ["avatar", "world"])

    def test_single_target(self) -> None:
        self.assertEqual(_resolve_targets(["avatar"]), ["avatar"])

    def test_dedup(self) -> None:
        self.assertEqual(_resolve_targets(["avatar", "avatar"]), ["avatar"])

    def test_all_dedup(self) -> None:
        self.assertEqual(_resolve_targets(["all", "avatar"]), ["avatar", "world"])

    def test_order_preserved(self) -> None:
        self.assertEqual(_resolve_targets(["world", "avatar"]), ["world", "avatar"])

    def test_empty(self) -> None:
        self.assertEqual(_resolve_targets([]), [])

    def test_all_plus_world_dedup(self) -> None:
        self.assertEqual(_resolve_targets(["world", "all"]), ["world", "avatar"])


class LoadTimeoutProfileMapTests(unittest.TestCase):
    def _write_profile(self, tmpdir: str, payload: Any) -> Path:
        path = Path(tmpdir) / "profile.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_valid_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_profile(tmpdir, {
                "profiles": [
                    {"target": "avatar", "recommended_timeout_sec": 120},
                    {"target": "world", "recommended_timeout_sec": 300},
                ],
            })
            result = _load_timeout_profile_map(path)
        self.assertEqual(result, {"avatar": 120, "world": 300})

    def test_non_dict_root_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_profile(tmpdir, [1, 2])
            with self.assertRaises(ValueError):
                _load_timeout_profile_map(path)

    def test_missing_profiles_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_profile(tmpdir, {"other": 1})
            with self.assertRaises(ValueError):
                _load_timeout_profile_map(path)

    def test_non_list_profiles_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_profile(tmpdir, {"profiles": "bad"})
            with self.assertRaises(ValueError):
                _load_timeout_profile_map(path)

    def test_non_dict_entry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_profile(tmpdir, {"profiles": ["not_dict"]})
            with self.assertRaises(ValueError):
                _load_timeout_profile_map(path)

    def test_invalid_target_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_profile(tmpdir, {
                "profiles": [{"target": "unknown", "recommended_timeout_sec": 100}],
            })
            with self.assertRaises(ValueError):
                _load_timeout_profile_map(path)

    def test_non_int_timeout_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_profile(tmpdir, {
                "profiles": [{"target": "avatar", "recommended_timeout_sec": "abc"}],
            })
            with self.assertRaises(ValueError):
                _load_timeout_profile_map(path)

    def test_zero_timeout_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_profile(tmpdir, {
                "profiles": [{"target": "avatar", "recommended_timeout_sec": 0}],
            })
            with self.assertRaises(ValueError):
                _load_timeout_profile_map(path)

    def test_negative_timeout_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_profile(tmpdir, {
                "profiles": [{"target": "avatar", "recommended_timeout_sec": -10}],
            })
            with self.assertRaises(ValueError):
                _load_timeout_profile_map(path)

    def test_missing_target_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_profile(tmpdir, {
                "profiles": [{"recommended_timeout_sec": 100}],
            })
            with self.assertRaises(ValueError):
                _load_timeout_profile_map(path)


class ResolveCaseUnityTimeoutSecTests(unittest.TestCase):
    def test_per_target_override_avatar(self) -> None:
        case = _make_case(name="avatar")
        timeout, source = _resolve_case_unity_timeout_sec(
            case=case, default_timeout_sec=60, avatar_timeout_sec=120,
            world_timeout_sec=None, timeout_profile_overrides={},
        )
        self.assertEqual(timeout, 120)
        self.assertEqual(source, "target_override")

    def test_per_target_override_world(self) -> None:
        case = _make_case(name="world")
        timeout, source = _resolve_case_unity_timeout_sec(
            case=case, default_timeout_sec=60, avatar_timeout_sec=None,
            world_timeout_sec=200, timeout_profile_overrides={},
        )
        self.assertEqual(timeout, 200)
        self.assertEqual(source, "target_override")

    def test_default_override(self) -> None:
        case = _make_case(name="avatar")
        timeout, source = _resolve_case_unity_timeout_sec(
            case=case, default_timeout_sec=60, avatar_timeout_sec=None,
            world_timeout_sec=None, timeout_profile_overrides={},
        )
        self.assertEqual(timeout, 60)
        self.assertEqual(source, "default_override")

    def test_profile_override(self) -> None:
        case = _make_case(name="avatar")
        timeout, source = _resolve_case_unity_timeout_sec(
            case=case, default_timeout_sec=None, avatar_timeout_sec=None,
            world_timeout_sec=None, timeout_profile_overrides={"avatar": 90},
        )
        self.assertEqual(timeout, 90)
        self.assertEqual(source, "profile")

    def test_none_fallback(self) -> None:
        case = _make_case(name="avatar")
        timeout, source = _resolve_case_unity_timeout_sec(
            case=case, default_timeout_sec=None, avatar_timeout_sec=None,
            world_timeout_sec=None, timeout_profile_overrides={},
        )
        self.assertIsNone(timeout)
        self.assertEqual(source, "none")

    def test_priority_target_over_default(self) -> None:
        case = _make_case(name="avatar")
        timeout, _ = _resolve_case_unity_timeout_sec(
            case=case, default_timeout_sec=60, avatar_timeout_sec=120,
            world_timeout_sec=None, timeout_profile_overrides={"avatar": 90},
        )
        self.assertEqual(timeout, 120)

    def test_priority_default_over_profile(self) -> None:
        case = _make_case(name="avatar")
        timeout, source = _resolve_case_unity_timeout_sec(
            case=case, default_timeout_sec=60, avatar_timeout_sec=None,
            world_timeout_sec=None, timeout_profile_overrides={"avatar": 90},
        )
        self.assertEqual(timeout, 60)
        self.assertEqual(source, "default_override")


def _build_cmd(
    *,
    case: SmokeCase | None = None,
    unity_command: str | None = None,
    unity_timeout_sec: int | None = None,
) -> list[str]:
    if case is None:
        case = _make_case()
    return _build_smoke_command(
        smoke_script=Path("scripts/smoke.py"),
        python_executable="python3",
        bridge_script=Path("tools/bridge.py"),
        unity_command=unity_command,
        unity_execute_method="Foo.Bar",
        unity_timeout_sec=unity_timeout_sec,
        case=case,
        response_out=Path("/tmp/response.json"),
        unity_log_file=Path("/tmp/unity.log"),
    )


class BuildSmokeCommandTests(unittest.TestCase):
    def test_minimal_command(self) -> None:
        cmd = _build_cmd()
        self.assertIn("python3", cmd)
        self.assertIn("scripts/smoke.py", cmd)
        self.assertNotIn("--unity-command", cmd)
        self.assertNotIn("--unity-timeout-sec", cmd)
        self.assertNotIn("--expect-failure", cmd)
        self.assertNotIn("--expected-code", cmd)

    def test_all_optional_flags(self) -> None:
        case = _make_case(expect_failure=True, expected_code="BRIDGE_FAIL")
        cmd = _build_cmd(case=case, unity_command="/usr/bin/Unity", unity_timeout_sec=300)
        self.assertIn("--unity-command", cmd)
        self.assertIn("/usr/bin/Unity", cmd)
        self.assertIn("--unity-timeout-sec", cmd)
        self.assertIn("300", cmd)
        self.assertIn("--expect-failure", cmd)
        self.assertIn("--expected-code", cmd)
        self.assertIn("BRIDGE_FAIL", cmd)

    def test_command_structure(self) -> None:
        case = _make_case()
        cmd = _build_cmd(case=case)
        self.assertEqual(cmd[0], "python3")
        self.assertEqual(cmd[1], "scripts/smoke.py")
        plan_idx = cmd.index("--plan")
        self.assertEqual(cmd[plan_idx + 1], str(case.plan))


class ParseCasePayloadTests(unittest.TestCase):
    def test_valid_json(self) -> None:
        payload = {"success": True, "code": "OK"}
        result = _parse_case_payload(
            case=_make_case(), exit_code=0,
            stdout_text=json.dumps(payload), stderr_text="",
        )
        self.assertEqual(result, payload)

    def test_invalid_json(self) -> None:
        result = _parse_case_payload(
            case=_make_case(), exit_code=1,
            stdout_text="not json", stderr_text="err",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "SMOKE_BATCH_STDOUT_JSON")
        self.assertEqual(result["data"]["target"], "avatar")
        self.assertEqual(result["data"]["exit_code"], 1)

    def test_non_dict_json(self) -> None:
        result = _parse_case_payload(
            case=_make_case(), exit_code=0,
            stdout_text="[1,2,3]", stderr_text="",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "SMOKE_BATCH_STDOUT_SCHEMA")

    def test_empty_stdout(self) -> None:
        result = _parse_case_payload(
            case=_make_case(), exit_code=0,
            stdout_text="", stderr_text="",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "SMOKE_BATCH_STDOUT_JSON")


class ExtractAppliedCountTests(unittest.TestCase):
    def test_valid_int(self) -> None:
        self.assertEqual(_extract_applied_count({"data": {"applied": 5}}), 5)

    def test_bool_is_int_subclass(self) -> None:
        # Unlike bridge_smoke.extract_applied_count, this version has no bool guard.
        # bool is a subclass of int in Python, so True → 1.
        self.assertEqual(_extract_applied_count({"data": {"applied": True}}), True)

    def test_non_dict_data(self) -> None:
        self.assertIsNone(_extract_applied_count({"data": "bad"}))

    def test_missing_data(self) -> None:
        self.assertIsNone(_extract_applied_count({}))

    def test_missing_applied(self) -> None:
        self.assertIsNone(_extract_applied_count({"data": {}}))

    def test_zero_applied(self) -> None:
        self.assertEqual(_extract_applied_count({"data": {"applied": 0}}), 0)


class RenderMarkdownSummaryTests(unittest.TestCase):
    def test_header_present(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "SMOKE_BATCH_OK",
            "message": "All passed.",
            "data": {"total_cases": 0, "passed_cases": 0, "failed_cases": 0, "cases": []},
        }
        md = _render_markdown_summary(payload)
        self.assertIn("# Unity Bridge Smoke Batch", md)
        self.assertIn("Success: True", md)
        self.assertIn("Total: 0", md)

    def test_case_row_rendered(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "OK",
            "message": "ok",
            "data": {
                "total_cases": 1,
                "passed_cases": 1,
                "failed_cases": 0,
                "cases": [
                    {
                        "name": "avatar",
                        "matched_expectation": True,
                        "expected_code": "OK",
                        "actual_code": "OK",
                        "code_matches": True,
                        "expected_applied": 3,
                        "expected_applied_source": "cli",
                        "actual_applied": 3,
                        "applied_matches": True,
                        "attempts": 1,
                        "duration_sec": 1.5,
                        "unity_timeout_sec": 120,
                        "timeout_source": "default",
                        "exit_code": 0,
                        "response_code": "OK",
                        "response_path": "/tmp/r.json",
                        "unity_log_file": "/tmp/u.log",
                    }
                ],
            },
        }
        md = _render_markdown_summary(payload)
        self.assertIn("| avatar |", md)
        self.assertIn("| True |", md)

    def test_timeout_profile_rendered(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "OK",
            "message": "ok",
            "data": {
                "total_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
                "timeout_profile_path": "/tmp/profile.json",
                "cases": [],
            },
        }
        md = _render_markdown_summary(payload)
        self.assertIn("Timeout Profile: /tmp/profile.json", md)

    def test_no_timeout_profile(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "OK",
            "message": "ok",
            "data": {"total_cases": 0, "passed_cases": 0, "failed_cases": 0, "cases": []},
        }
        md = _render_markdown_summary(payload)
        self.assertIn("Timeout Profile: n/a", md)

    def test_none_values_rendered_as_empty(self) -> None:
        payload = {
            "success": True,
            "severity": "info",
            "code": "OK",
            "message": "ok",
            "data": {
                "total_cases": 1,
                "passed_cases": 1,
                "failed_cases": 0,
                "cases": [
                    {
                        "name": "avatar",
                        "matched_expectation": True,
                        "expected_code": None,
                        "actual_code": None,
                        "code_matches": None,
                        "expected_applied": None,
                        "expected_applied_source": None,
                        "actual_applied": None,
                        "applied_matches": None,
                        "attempts": 1,
                        "duration_sec": None,
                        "unity_timeout_sec": None,
                        "timeout_source": "",
                        "exit_code": 0,
                        "response_code": "",
                        "response_path": "",
                        "unity_log_file": "",
                    }
                ],
            },
        }
        md = _render_markdown_summary(payload)
        self.assertIn("| avatar |", md)


if __name__ == "__main__":
    unittest.main()
