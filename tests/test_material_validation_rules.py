import json
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.material_validation_rules import MaterialValidationRules, load_material_validation_rules


def _write_rules_config(project_root: Path, payload: object) -> Path:
    config_path = project_root / "config" / "material_validation_rules.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


class TestMaterialValidationRules(unittest.TestCase):
    def test_absent_config_returns_project_neutral_empty_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = load_material_validation_rules(Path(tmp))

        self.assertEqual(
            "absent",
            result.status,
            msg="absent config should be reported as a valid absent rule set",
        )
        self.assertIsNotNone(
            result.rules,
            msg="absent config should still return an empty rule model",
        )
        rules = result.rules
        if rules is None:
            raise AssertionError("absent config should still return an empty rule model")
        self.assertEqual(
            ((), (), (), ()),
            (
                rules.shader_name_policies,
                rules.shared_material_groups,
                rules.folder_policies,
                result.diagnostics,
            ),
            msg="absent config must not synthesize project-specific policies",
        )

    def test_malformed_json_returns_invalid_result_with_config_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            config_path = project_root / "config" / "material_validation_rules.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("{", encoding="utf-8")

            result = load_material_validation_rules(project_root)

        self.assertEqual(
            ("invalid", None),
            (result.status, result.rules),
            msg="malformed JSON must not produce a partially loaded rule model",
        )
        self.assertEqual(
            [("MATERIAL_RULES_INVALID", str(config_path), "error")],
            [
                (diag.detail, diag.path, diag.severity)
                for diag in result.diagnostics
            ],
            msg="invalid config should identify the config path and error code",
        )

    def test_unsupported_version_returns_invalid_result_with_config_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            config_path = _write_rules_config(project_root, {"version": 2})

            result = load_material_validation_rules(project_root)

        self.assertEqual(
            ("invalid", None),
            (result.status, result.rules),
            msg="unsupported versions must not be accepted as loaded rules",
        )
        self.assertEqual(
            [("MATERIAL_RULES_INVALID", str(config_path), "error")],
            [
                (diag.detail, diag.path, diag.severity)
                for diag in result.diagnostics
            ],
            msg="version schema errors should use the material rules code",
        )

    def test_symlinked_rules_config_is_rejected_before_reading_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            project_root = Path(tmp)
            outside_config = Path(outside) / "material_validation_rules.json"
            outside_config.write_text(
                json.dumps({"version": 1, "shader_name_policies": []}),
                encoding="utf-8",
            )
            config_path = project_root / "config" / "material_validation_rules.json"
            config_path.parent.mkdir(parents=True)
            try:
                config_path.symlink_to(outside_config)
            except (OSError, NotImplementedError):
                self.skipTest("platform does not support symlink creation")

            result = load_material_validation_rules(project_root)

        self.assertEqual(
            ("invalid", None),
            (result.status, result.rules),
            msg="symlinked rules config must not be parsed from outside the project",
        )
        self.assertEqual(
            [("MATERIAL_RULES_INVALID", str(config_path), "error")],
            [
                (diag.detail, diag.path, diag.severity)
                for diag in result.diagnostics
            ],
            msg="symlinked rules config should fail at the rule-loader boundary",
        )

    def test_folder_policy_without_disallowed_selectors_loads_as_noop_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            _write_rules_config(
                project_root,
                {
                    "version": 1,
                    "folder_policies": [
                        {
                            "id": "fonts-policy-placeholder",
                            "folder": "Assets/Fonts",
                        }
                    ],
                },
            )

            result = load_material_validation_rules(project_root)

        rules = result.rules
        self.assertIsNotNone(
            rules,
            msg="selector-less folder policy should still produce a loaded rule model",
        )
        if rules is None:
            raise AssertionError("selector-less folder policy should load")
        self.assertEqual(
            (
                "loaded",
                (("fonts-policy-placeholder", "Assets/Fonts", (), ()),),
                (),
            ),
            (
                result.status,
                tuple(
                    (
                        policy.id,
                        policy.folder,
                        policy.disallowed_extensions,
                        policy.disallowed_asset_kinds,
                    )
                    for policy in rules.folder_policies
                ),
                result.diagnostics,
            ),
            msg="empty selector lists are an explicit no-op, not a schema error",
        )

    def test_shader_and_shared_rules_accept_omitted_hierarchy_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            _write_rules_config(
                project_root,
                {
                    "version": 1,
                    "shader_name_policies": [
                        {
                            "id": "all-ui-shader",
                            "scope": "Assets/UI",
                            "expected_shader": "UI/Overlay/AlwaysOnTop",
                        }
                    ],
                    "shared_material_groups": [
                        {
                            "id": "all-ui-shared-materials",
                            "scope": "Assets/UI",
                        }
                    ],
                },
            )

            result = load_material_validation_rules(project_root)

        rules = result.rules
        self.assertIsNotNone(
            rules,
            msg="omitted hierarchy_prefix should load as a scope-wide policy",
        )
        if rules is None:
            raise AssertionError("omitted hierarchy_prefix should load")
        self.assertEqual(
            (
                "loaded",
                (("all-ui-shader", "Assets/UI", "", "UI/Overlay/AlwaysOnTop"),),
                (("all-ui-shared-materials", "Assets/UI", "", None),),
                (),
            ),
            (
                result.status,
                tuple(
                    (
                        policy.id,
                        policy.scope,
                        policy.hierarchy_prefix,
                        policy.expected_shader,
                    )
                    for policy in rules.shader_name_policies
                ),
                tuple(
                    (
                        group.id,
                        group.scope,
                        group.hierarchy_prefix,
                        group.expected_material,
                    )
                    for group in rules.shared_material_groups
                ),
                result.diagnostics,
            ),
            msg="missing hierarchy_prefix should be normalized to an empty prefix",
        )

    def test_valid_config_loads_all_supported_rule_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            _write_rules_config(
                project_root,
                {
                    "version": 1,
                    "shader_name_policies": [
                        {
                            "id": "overlay-ui-shader",
                            "scope": "Assets/UI",
                            "hierarchy_prefix": "Canvas/Overlay",
                            "expected_shader": "UI/Overlay/AlwaysOnTop",
                        }
                    ],
                    "shared_material_groups": [
                        {
                            "id": "overlay-icons",
                            "scope": "Assets/UI",
                            "hierarchy_prefix": "Canvas/Overlay/Icons",
                        }
                    ],
                    "folder_policies": [
                        {
                            "id": "fonts-no-materials",
                            "folder": "Assets/Fonts",
                            "disallowed_extensions": [".mat"],
                            "disallowed_asset_kinds": ["Material"],
                        }
                    ],
                },
            )

            result = load_material_validation_rules(project_root)

        rules: MaterialValidationRules | None = result.rules
        self.assertIsNotNone(
            rules,
            msg="valid config should return a loaded rule model",
        )
        if rules is None:
            raise AssertionError("valid config should return a loaded rule model")
        self.assertEqual(
            ("loaded", ()),
            (result.status, result.diagnostics),
            msg="valid config should load without diagnostics",
        )
        self.assertEqual(
            (
                (("overlay-ui-shader", "Assets/UI", "Canvas/Overlay", "UI/Overlay/AlwaysOnTop"),),
                (("overlay-icons", "Assets/UI", "Canvas/Overlay/Icons", None),),
                (("fonts-no-materials", "Assets/Fonts", (".mat",), ("Material",)),),
            ),
            (
                tuple(
                    (
                        policy.id,
                        policy.scope,
                        policy.hierarchy_prefix,
                        policy.expected_shader,
                    )
                    for policy in rules.shader_name_policies
                ),
                tuple(
                    (
                        group.id,
                        group.scope,
                        group.hierarchy_prefix,
                        group.expected_material,
                    )
                    for group in rules.shared_material_groups
                ),
                tuple(
                    (
                        policy.id,
                        policy.folder,
                        policy.disallowed_extensions,
                        policy.disallowed_asset_kinds,
                    )
                    for policy in rules.folder_policies
                ),
            ),
            msg="valid config should preserve supported rule family fields",
        )

    def test_configuration_reference_example_loads_as_rules(self) -> None:
        config_doc = Path("CONFIGURATION.md").read_text(encoding="utf-8")
        section_start = config_doc.index("## material_validation_rules.json")
        block_start = config_doc.index("```json\n", section_start) + len("```json\n")
        block_end = config_doc.index("\n```", block_start)
        payload = json.loads(config_doc[block_start:block_end])

        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            _write_rules_config(project_root, payload)

            result = load_material_validation_rules(project_root)

        rules = result.rules
        self.assertIsNotNone(
            rules,
            msg="CONFIGURATION.md example should produce a loaded rule model",
        )
        if rules is None:
            raise AssertionError("CONFIGURATION.md example should produce a loaded rule model")
        self.assertEqual(
            ("loaded", 1, 1, 1, ()),
            (
                result.status,
                len(rules.shader_name_policies),
                len(rules.shared_material_groups),
                len(rules.folder_policies),
                result.diagnostics,
            ),
            msg="published material validation rules example should match loader schema",
        )


if __name__ == "__main__":
    unittest.main()
