"""Regression contracts for explicit JSON resource authority (issue #204)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.orchestrator import Phase1Orchestrator


class ExplicitJsonKindAuthorityTests(unittest.TestCase):
    def _plan(
        self,
        *,
        path: str,
        include_kind: bool = True,
    ) -> dict[str, object]:
        resource: dict[str, str] = {
            "id": "manifest",
            "path": path,
            "mode": "open",
        }
        if include_kind:
            resource["kind"] = "json"
        return {
            "plan_version": 2,
            "resources": [resource],
            "ops": [
                {
                    "resource": "manifest",
                    "op": "set",
                    "path": "name",
                    "value": "After",
                }
            ],
        }

    def test_explicit_json_kind_overrides_asmdef_suffix_in_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "Assets" / "Example.asmdef"
            target.parent.mkdir()
            target.write_text(json.dumps({"name": "Before"}), encoding="utf-8")
            before_bytes = target.read_bytes()

            result = Phase1Orchestrator.default(project_root=root).patch_apply(
                plan=self._plan(path="Assets/Example.asmdef"),
                dry_run=True,
            )

            self.assertEqual(
                (True, "json", before_bytes),
                (
                    result.success,
                    result.data["resources"][0]["kind"],
                    target.read_bytes(),
                ),
            )
            self.assertEqual(
                [
                    {
                        "op": "set",
                        "component": "",
                        "path": "name",
                        "before": "Before",
                        "after": "After",
                    }
                ],
                result.data["steps"][0]["result"]["data"]["diff"],
            )

    def test_explicit_json_kind_overrides_asmdef_suffix_on_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "Assets" / "Example.asmdef"
            target.parent.mkdir()
            target.write_text(json.dumps({"name": "Before"}), encoding="utf-8")

            result = Phase1Orchestrator.default(project_root=root).patch_apply(
                plan=self._plan(path="Assets/Example.asmdef"),
                dry_run=False,
                confirm=True,
                change_reason="Exercise explicit JSON resource authority",
            )

            self.assertEqual(
                (True, "After"),
                (
                    result.success,
                    json.loads(target.read_text(encoding="utf-8"))["name"],
                ),
            )
            self.assertIn(
                "SER_APPLY_OK",
                [step["result"]["code"] for step in result.data["steps"]],
            )

    def test_ordinary_json_resource_keeps_json_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "state.json"
            target.write_text(json.dumps({"name": "Before"}), encoding="utf-8")
            before_bytes = target.read_bytes()

            dry_run = Phase1Orchestrator.default(project_root=root).patch_apply(
                plan=self._plan(path="state.json"),
                dry_run=True,
            )
            after_dry_run_bytes = target.read_bytes()

            result = Phase1Orchestrator.default(project_root=root).patch_apply(
                plan=self._plan(path="state.json"),
                dry_run=False,
                confirm=True,
                change_reason="Exercise ordinary JSON resource",
            )

            self.assertEqual(
                (
                    True,
                    True,
                    [
                        {
                            "op": "set",
                            "component": "",
                            "path": "name",
                            "before": "Before",
                            "after": "After",
                        }
                    ],
                    before_bytes,
                    "After",
                ),
                (
                    dry_run.success,
                    result.success,
                    dry_run.data["steps"][0]["result"]["data"]["diff"],
                    after_dry_run_bytes,
                    json.loads(target.read_text(encoding="utf-8"))["name"],
                ),
            )

    def test_omitted_kind_keeps_asmdef_suffix_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "Assets" / "Example.asmdef"
            target.parent.mkdir()
            target.write_text(json.dumps({"name": "Before"}), encoding="utf-8")
            before_bytes = target.read_bytes()

            result = Phase1Orchestrator.default(project_root=root).patch_apply(
                plan=self._plan(
                    path="Assets/Example.asmdef",
                    include_kind=False,
                ),
                dry_run=True,
            )

            self.assertEqual(
                (False, "asset", before_bytes),
                (
                    result.success,
                    result.data["resources"][0]["kind"],
                    target.read_bytes(),
                ),
            )
            self.assertIn(
                "asset open mode requires a .asset target path",
                [diagnostic.evidence for diagnostic in result.diagnostics],
            )

    def test_explicit_json_rejects_missing_required_value_before_confirm(self) -> None:
        cases = (
            ({"op": "set", "path": "name"}, "value is required for set"),
            (
                {
                    "op": "insert_array_element",
                    "path": "items.Array.data",
                    "index": 0,
                },
                "value is required for insert_array_element",
            ),
        )
        for operation, expected_evidence in cases:
            with self.subTest(op=operation["op"]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / "Assets" / "Example.asmdef"
                target.parent.mkdir()
                target.write_text(
                    json.dumps({"name": "Before", "items": [1]}),
                    encoding="utf-8",
                )
                before_bytes = target.read_bytes()
                plan = self._plan(path="Assets/Example.asmdef")
                plan["ops"] = [{"resource": "manifest", **operation}]

                dry_run = Phase1Orchestrator.default(project_root=root).patch_apply(
                    plan=plan,
                    dry_run=True,
                )
                confirmed = Phase1Orchestrator.default(project_root=root).patch_apply(
                    plan=plan,
                    dry_run=False,
                    confirm=True,
                    change_reason="Reject malformed explicit JSON operation",
                )

                self.assertEqual(
                    (False, False, before_bytes),
                    (dry_run.success, confirmed.success, target.read_bytes()),
                )
                self.assertIn(
                    expected_evidence,
                    [diagnostic.evidence for diagnostic in dry_run.diagnostics],
                )
                self.assertIn(
                    expected_evidence,
                    [diagnostic.evidence for diagnostic in confirmed.diagnostics],
                )


if __name__ == "__main__":
    unittest.main()
