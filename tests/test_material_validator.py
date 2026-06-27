import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import prefab_sentinel.orchestrator as orchestrator_module
from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.material_validation_rules import (
    FolderPolicy,
    MaterialValidationRules,
    ShaderNamePolicy,
    SharedMaterialGroup,
    load_material_validation_rules,
)
from prefab_sentinel.material_validator import validate_materials as core_validate
from prefab_sentinel.orchestrator import Phase1Orchestrator
from prefab_sentinel.orchestrator_material_validation import (
    validate_materials as orchestrate_validate,
)
from prefab_sentinel.services.reference_resolver import ReferenceResolverService

GOOD_SHADER_GUID = "a" * 32
BAD_SHADER_GUID = "b" * 32
MATCH_MATERIAL_GUID = "c" * 32
MISMATCH_MATERIAL_GUID = "d" * 32
TMP_MATERIAL_GUID = "e" * 32
FONT_GUID = "f" * 32
ATLAS_GUID = "1" * 32
UNRESOLVED_GUID = "2" * 32


def _project() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory()


def _project_root(tmp: str) -> Path:
    root = Path(tmp)
    (root / "Assets").mkdir()
    return root


def _write_asset(
    project_root: Path,
    relative_path: str,
    text: str,
    *,
    guid: str | None = None,
) -> Path:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if guid is not None:
        _write_meta(path, guid)
    return path


def _write_binary_asset(project_root: Path, relative_path: str, data: bytes) -> Path:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _write_meta(asset_path: Path, guid: str) -> None:
    asset_path.with_suffix(asset_path.suffix + ".meta").write_text(
        f"fileFormatVersion: 2\nguid: {guid}\n",
        encoding="utf-8",
    )


def _write_shader_meta(project_root: Path, relative_path: str, guid: str) -> None:
    shader_path = project_root / relative_path
    shader_path.parent.mkdir(parents=True, exist_ok=True)
    _write_meta(shader_path, guid)


def _rules_empty() -> MaterialValidationRules:
    return MaterialValidationRules.empty(None)


def _shader_policy(expected_shader: str = "Expected/Shader") -> MaterialValidationRules:
    return MaterialValidationRules(
        config_status="loaded",
        config_path=None,
        shader_name_policies=(
            ShaderNamePolicy(
                id="ui-shader",
                scope="Assets/UI",
                hierarchy_prefix="Canvas/Icon",
                expected_shader=expected_shader,
            ),
        ),
        shared_material_groups=(),
        folder_policies=(),
    )


def _shared_group(expected_material: str | None) -> MaterialValidationRules:
    return MaterialValidationRules(
        config_status="loaded",
        config_path=None,
        shader_name_policies=(),
        shared_material_groups=(
            SharedMaterialGroup(
                id="icons",
                scope="Assets/UI",
                hierarchy_prefix="Canvas/Icon",
                expected_material=expected_material,
            ),
        ),
        folder_policies=(),
    )


def _folder_policy() -> MaterialValidationRules:
    return MaterialValidationRules(
        config_status="loaded",
        config_path=None,
        shader_name_policies=(),
        shared_material_groups=(),
        folder_policies=(
            FolderPolicy(
                id="fonts-no-materials",
                folder="Assets/Fonts",
                disallowed_extensions=(".mat",),
                disallowed_asset_kinds=("Material",),
            ),
        ),
    )


def _material_yaml(
    name: str,
    *,
    shader_guid: str | None = GOOD_SHADER_GUID,
    render_queue: int | None = 2450,
    ztest: int | None = 8,
    texture_guid: str | None = None,
) -> str:
    shader_ref = (
        "{fileID: 0}"
        if shader_guid is None
        else f"{{fileID: 4800000, guid: {shader_guid}, type: 3}}"
    )
    render_queue_line = (
        "" if render_queue is None else f"  m_CustomRenderQueue: {render_queue}\n"
    )
    ztest_line = "" if ztest is None else f"    - _ZTest: {ztest}\n"
    texture_ref = (
        "{fileID: 0}"
        if texture_guid is None
        else f"{{fileID: 2800000, guid: {texture_guid}, type: 3}}"
    )
    return f"""%YAML 1.1
--- !u!21 &2100000
Material:
  m_Name: {name}
  m_Shader: {shader_ref}
{render_queue_line}  m_SavedProperties:
    m_TexEnvs:
    - _MainTex:
        m_Texture: {texture_ref}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    m_Floats:
{ztest_line}    m_Colors: []
    m_Ints: []
"""


def _prefab_with_renderers(
    material_guids: tuple[str, ...],
    *,
    tmp_font_guid: str | None = None,
    tmp_material_guid: str | None = None,
) -> str:
    children = "\n".join(
        f"  - {{fileID: {2001 + index}}}" for index in range(len(material_guids))
    )
    child_blocks = "\n".join(
        _child_renderer_block(index, guid, tmp_font_guid, tmp_material_guid)
        for index, guid in enumerate(material_guids)
    )
    return f"""%YAML 1.1
--- !u!1 &1000
GameObject:
  m_Name: Canvas
  m_Component:
  - component: {{fileID: 2000}}
--- !u!4 &2000
Transform:
  m_GameObject: {{fileID: 1000}}
  m_Father: {{fileID: 0}}
  m_Children:
{children}
{child_blocks}
"""


def _child_renderer_block(
    index: int,
    material_guid: str,
    tmp_font_guid: str | None,
    tmp_material_guid: str | None,
) -> str:
    go_id = 1001 + index
    transform_id = 2001 + index
    renderer_id = 3001 + index
    tmp_id = 4001 + index
    tmp_component = "" if tmp_font_guid is None else f"  - component: {{fileID: {tmp_id}}}\n"
    tmp_block = (
        ""
        if tmp_font_guid is None or tmp_material_guid is None
        else f"""--- !u!114 &{tmp_id}
MonoBehaviour:
  m_GameObject: {{fileID: {go_id}}}
  m_FontAsset: {{fileID: 11400000, guid: {tmp_font_guid}, type: 2}}
  m_sharedMaterial: {{fileID: 2100000, guid: {tmp_material_guid}, type: 2}}
"""
    )
    return f"""--- !u!1 &{go_id}
GameObject:
  m_Name: Icon{index}
  m_Component:
  - component: {{fileID: {transform_id}}}
  - component: {{fileID: {renderer_id}}}
{tmp_component}--- !u!4 &{transform_id}
Transform:
  m_GameObject: {{fileID: {go_id}}}
  m_Father: {{fileID: 2000}}
  m_Children: []
--- !u!23 &{renderer_id}
MeshRenderer:
  m_GameObject: {{fileID: {go_id}}}
  m_Materials:
  - {{fileID: 2100000, guid: {material_guid}, type: 2}}
{tmp_block}"""


def _font_asset_yaml(*, atlas_guid: str | None) -> str:
    atlas_block = (
        "  m_AtlasTextures: []\n"
        if atlas_guid is None
        else f"  m_AtlasTextures:\n  - {{fileID: 2800000, guid: {atlas_guid}, type: 3}}\n"
    )
    return f"""%YAML 1.1
--- !u!114 &11400000
MonoBehaviour:
  m_Name: IconFont
{atlas_block}"""


def _material_diag_codes(response: dict[str, Any]) -> list[str]:
    return [diag["code"] for diag in response["diagnostics"]]


def _response_dict(response: Any) -> dict[str, Any]:
    return cast(dict[str, Any], response.to_dict())


class TestMaterialValidatorScopeAndRules(unittest.TestCase):
    def test_missing_scope_path_is_reported_as_error(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(orchestrate_validate(resolver, "Assets/Missing"))

        self.assertEqual(
            (False, "error", "MATERIAL_VALIDATION_SCOPE_NOT_FOUND"),
            (response["success"], response["severity"], response["code"]),
            msg="missing scope should fail before validation",
        )

    def test_outside_project_scope_is_reported_as_error(self) -> None:
        with _project() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            project_root = _project_root(tmp)
            outside_path = Path(outside_tmp)
            _write_asset(outside_path, "Escaped.mat", _material_yaml("Escaped"))
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(orchestrate_validate(resolver, str(outside_path)))

        self.assertEqual(
            (False, "error", "MATERIAL_VALIDATION_SCOPE_NOT_FOUND", []),
            (
                response["success"],
                response["severity"],
                response["code"],
                response["data"].get("details", []),
            ),
            msg="outside scopes must not be scanned",
        )

    def test_project_root_scope_is_reported_as_error(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            _write_asset(
                project_root,
                "Assets/UI/Root.mat",
                _material_yaml("Root"),
                guid=MATCH_MATERIAL_GUID,
            )
            resolver = ReferenceResolverService(project_root=project_root)

            responses = [
                _response_dict(orchestrate_validate(resolver, scope))
                for scope in (".", str(project_root))
            ]

        self.assertEqual(
            [
                (False, "error", "MATERIAL_VALIDATION_SCOPE_NOT_FOUND"),
                (False, "error", "MATERIAL_VALIDATION_SCOPE_NOT_FOUND"),
            ],
            [
                (response["success"], response["severity"], response["code"])
                for response in responses
            ],
            msg="project-root scopes must not be accepted as material validation scopes",
        )

    def test_invalid_rules_config_stops_before_validation_findings(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            config_path = project_root / "config" / "material_validation_rules.json"
            config_path.parent.mkdir()
            config_path.write_text("{", encoding="utf-8")
            _write_asset(
                project_root,
                "Assets/UI/MissingShader.mat",
                _material_yaml("MissingShader", shader_guid=None),
            )
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(orchestrate_validate(resolver, "Assets/UI"))

        self.assertEqual(
            (False, "error", "MATERIAL_RULES_INVALID"),
            (response["success"], response["severity"], response["code"]),
            msg="invalid config should be an orchestration error",
        )
        self.assertEqual(
            [("MATERIAL_RULES_INVALID", str(config_path))],
            [(diag["code"], diag["data"]["path"]) for diag in response["diagnostics"]],
            msg="invalid config should not continue to material diagnostics",
        )

    def test_file_scope_limits_validation_to_selected_asset(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            _write_shader_meta(project_root, "Assets/Shaders/Bad.shader", BAD_SHADER_GUID)
            _write_shader_meta(project_root, "Assets/Shaders/Other.shader", GOOD_SHADER_GUID)
            scoped = _write_asset(
                project_root,
                "Assets/UI/Scoped.mat",
                _material_yaml("Scoped", shader_guid=BAD_SHADER_GUID),
                guid=MATCH_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/Other.mat",
                _material_yaml("Other", shader_guid=GOOD_SHADER_GUID),
                guid=MISMATCH_MATERIAL_GUID,
            )
            _write_rules(project_root, _shader_rules_payload("ExpectedShader"))
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(orchestrate_validate(resolver, str(scoped)))

        self.assertEqual(
            ("MATERIAL_VALIDATION_FINDINGS", ["Assets/UI/Scoped.mat"], "loaded"),
            (
                response["code"],
                [diag["data"]["path"] for diag in response["diagnostics"]],
                response["data"]["rule_config"]["status"],
            ),
            msg="file scope should not widen to sibling material assets",
        )

    def test_empty_supported_scope_returns_ok_with_zero_targets(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            (project_root / "Assets" / "Empty").mkdir()
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(orchestrate_validate(resolver, "Assets/Empty"))

        self.assertEqual(
            (True, "info", "MATERIAL_VALIDATION_OK", 0, []),
            (
                response["success"],
                response["severity"],
                response["code"],
                response["data"]["summary"]["scanned_targets"],
                response["diagnostics"],
            ),
            msg="empty valid scope should be a clean zero-target result",
        )

    def test_present_empty_rules_config_reports_loaded_status(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            (project_root / "Assets" / "Empty").mkdir()
            _write_rules(project_root, {"version": 1})
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(orchestrate_validate(resolver, "Assets/Empty"))

        self.assertEqual(
            ("loaded", 0, 0, 0),
            (
                response["data"]["rule_config"]["status"],
                response["data"]["rule_config"]["shader_name_policies"],
                response["data"]["rule_config"]["shared_material_groups"],
                response["data"]["rule_config"]["folder_policies"],
            ),
            msg="present empty config should stay loaded, not be inferred as absent",
        )

    def test_core_rule_config_preserves_loaded_empty_rule_status(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "Empty"
            scope.mkdir(parents=True)
            _write_rules(project_root, {"version": 1})
            rules_result = load_material_validation_rules(project_root)
            rules = rules_result.rules
            if rules is None:
                raise AssertionError("present empty config should produce rules")
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(core_validate(resolver, scope, rules))

        self.assertEqual(
            ("loaded", 0, 0, 0),
            (
                response["data"]["rule_config"]["status"],
                response["data"]["rule_config"]["shader_name_policies"],
                response["data"]["rule_config"]["shared_material_groups"],
                response["data"]["rule_config"]["folder_policies"],
            ),
            msg="core validator should use loader status instead of inferring from rule counts",
        )

    def test_clean_non_empty_scope_returns_ok_summary_counts(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            _write_shader_meta(project_root, "Assets/Shaders/Good.shader", GOOD_SHADER_GUID)
            _write_asset(
                project_root,
                "Assets/UI/Good.mat",
                _material_yaml("Good"),
                guid=MATCH_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/Clean.prefab",
                _prefab_with_renderers((MATCH_MATERIAL_GUID,)),
            )
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(orchestrate_validate(resolver, "Assets/UI"))

        self.assertEqual(
            (True, "info", "MATERIAL_VALIDATION_OK", 2, 1, 1, []),
            (
                response["success"],
                response["severity"],
                response["code"],
                response["data"]["summary"]["scanned_targets"],
                response["data"]["summary"]["materials"],
                response["data"]["summary"]["renderer_slots"],
                response["diagnostics"],
            ),
            msg="clean material and renderer evidence should stay successful",
        )


class TestMaterialValidatorGenericChecks(unittest.TestCase):
    def test_generic_shader_risks_do_not_include_project_policy_codes(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "UI"
            _write_asset(
                project_root,
                "Assets/UI/Missing.mat",
                _material_yaml("Missing", shader_guid=None),
            )
            _write_asset(
                project_root,
                "Assets/UI/Unresolved.mat",
                _material_yaml("Unresolved", shader_guid=UNRESOLVED_GUID),
            )
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(core_validate(resolver, scope, _rules_empty()))

        codes = _material_diag_codes(response)
        self.assertEqual(
            ("MATERIAL_VALIDATION_FINDINGS", "warning"),
            (response["code"], response["severity"]),
            msg="generic material shader risks should be warning findings",
        )
        self.assertEqual(
            ["MATERIAL_SHADER_MISSING", "MATERIAL_SHADER_UNRESOLVED"],
            sorted(codes),
            msg="generic mode should report only shader risk codes",
        )

    def test_renderer_slot_unresolved_material_reference_is_reported(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "UI"
            _write_asset(
                project_root,
                "Assets/UI/Broken.prefab",
                _prefab_with_renderers((UNRESOLVED_GUID,)),
            )
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(core_validate(resolver, scope, _rules_empty()))

        self.assertEqual(
            [("MATERIAL_SLOT_UNRESOLVED", "Assets/UI/Broken.prefab", "renderer_slot:Canvas/Icon0[0]")],
            [
                (diag["code"], diag["data"]["path"], diag["data"]["location"])
                for diag in response["diagnostics"]
            ],
            msg="broken renderer material slots should name source and slot",
        )


class TestMaterialValidatorRules(unittest.TestCase):
    def test_shader_policy_applies_to_material_renderer_and_tmp_sources(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "UI"
            _write_shader_meta(project_root, "Assets/Shaders/Bad.shader", BAD_SHADER_GUID)
            _write_shader_meta(project_root, "Assets/Shaders/ExpectedShader.shader", GOOD_SHADER_GUID)
            _write_asset(
                project_root,
                "Assets/UI/Direct.mat",
                _material_yaml("Direct", shader_guid=BAD_SHADER_GUID),
                guid=MATCH_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/Materials/TmpPreset.mat",
                _material_yaml("TmpPreset", shader_guid=BAD_SHADER_GUID),
                guid=TMP_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/Matched.mat",
                _material_yaml("Matched", shader_guid=GOOD_SHADER_GUID),
                guid=MISMATCH_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/IconFont.asset",
                _font_asset_yaml(atlas_guid=ATLAS_GUID),
                guid=FONT_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/Policy.prefab",
                _prefab_with_renderers(
                    (MATCH_MATERIAL_GUID,),
                    tmp_font_guid=FONT_GUID,
                    tmp_material_guid=TMP_MATERIAL_GUID,
                ),
            )
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(
                core_validate(
                    resolver,
                    scope,
                    _shader_policy(expected_shader="ExpectedShader"),
                )
            )

        self.assertEqual(
            [
                ("MATERIAL_SHADER_POLICY_MISMATCH", "material:Assets/UI/Direct.mat"),
                ("MATERIAL_SHADER_POLICY_MISMATCH", "renderer_slot:Canvas/Icon0[0]"),
                ("MATERIAL_SHADER_POLICY_MISMATCH", "tmp_material_preset:Canvas/Icon0"),
            ],
            [
                (diag["code"], diag["data"]["location"])
                for diag in response["diagnostics"]
                if diag["code"] == "MATERIAL_SHADER_POLICY_MISMATCH"
            ],
            msg="shader policy should apply across direct, renderer, and TMP evidence",
        )

    def test_expected_shared_material_group_reports_only_mismatches(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "UI"
            _write_shader_meta(project_root, "Assets/Shaders/Good.shader", GOOD_SHADER_GUID)
            _write_asset(
                project_root,
                "Assets/UI/Match.mat",
                _material_yaml("Match"),
                guid=MATCH_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/Mismatch.mat",
                _material_yaml("Mismatch"),
                guid=MISMATCH_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/Shared.prefab",
                _prefab_with_renderers((MATCH_MATERIAL_GUID, MISMATCH_MATERIAL_GUID)),
            )
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(
                core_validate(
                    resolver,
                    scope,
                    _shared_group("Assets/UI/Match.mat"),
                )
            )

        self.assertEqual(
            [("MATERIAL_SHARED_GROUP_MISMATCH", "renderer_slot:Canvas/Icon1[0]")],
            [
                (diag["code"], diag["data"]["location"])
                for diag in response["diagnostics"]
                if diag["code"].startswith("MATERIAL_SHARED")
            ],
            msg="expected shared-material rule should not warn for matching slots",
        )

    def test_shared_material_group_without_expected_reports_candidates_only(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "UI"
            _write_shader_meta(project_root, "Assets/Shaders/Good.shader", GOOD_SHADER_GUID)
            _write_asset(
                project_root,
                "Assets/UI/One.mat",
                _material_yaml("One"),
                guid=MATCH_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/Two.mat",
                _material_yaml("Two"),
                guid=MISMATCH_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/Drift.prefab",
                _prefab_with_renderers((MATCH_MATERIAL_GUID, MISMATCH_MATERIAL_GUID)),
            )
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(
                core_validate(resolver, scope, _shared_group(None))
            )

        drift_messages = [
            diag["message"]
            for diag in response["diagnostics"]
            if diag["code"] == "MATERIAL_SHARED_GROUP_DRIFT"
        ]
        self.assertEqual(
            1,
            len(drift_messages),
            msg="candidate-only shared-material drift should emit one group warning",
        )
        self.assertIn("Assets/UI/One.mat: 1", drift_messages[0])
        self.assertIn("Assets/UI/Two.mat: 1", drift_messages[0])
        self.assertNotIn("expected", drift_messages[0].lower())

    def test_folder_policy_reports_classifiable_material_and_ignores_unknown_kind(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets"
            _write_shader_meta(project_root, "Assets/Shaders/Good.shader", GOOD_SHADER_GUID)
            _write_asset(
                project_root,
                "Assets/Fonts/Bad.mat",
                _material_yaml("Bad"),
            )
            _write_asset(
                project_root,
                "Assets/Fonts/Unknown.asset",
                "%YAML 1.1\n--- !u!999999 &1\nUnknown:\n  m_Name: Unknown\n",
            )
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(core_validate(resolver, scope, _folder_policy()))

        self.assertEqual(
            [("MATERIAL_FOLDER_POLICY_VIOLATION", "Assets/Fonts/Bad.mat")],
            [
                (diag["code"], diag["data"]["path"])
                for diag in response["diagnostics"]
                if diag["code"] == "MATERIAL_FOLDER_POLICY_VIOLATION"
            ],
            msg="folder policy should stay silent for unclassifiable asset kinds",
        )


class TestMaterialValidatorTmpEvidence(unittest.TestCase):
    def test_tmp_static_evidence_is_returned_only_when_details_are_requested(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "UI"
            _write_shader_meta(project_root, "Assets/Shaders/Good.shader", GOOD_SHADER_GUID)
            _write_asset(
                project_root,
                "Assets/UI/TmpPreset.mat",
                _material_yaml("TmpPreset", texture_guid=ATLAS_GUID),
                guid=TMP_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/IconFont.asset",
                _font_asset_yaml(atlas_guid=ATLAS_GUID),
                guid=FONT_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/Tmp.prefab",
                _prefab_with_renderers(
                    (TMP_MATERIAL_GUID,),
                    tmp_font_guid=FONT_GUID,
                    tmp_material_guid=TMP_MATERIAL_GUID,
                ),
            )
            resolver = ReferenceResolverService(project_root=project_root)

            detailed = _response_dict(
                core_validate(resolver, scope, _rules_empty(), include_details=True)
            )
            default = _response_dict(core_validate(resolver, scope, _rules_empty()))

        tmp_detail = detailed["data"]["details"]["tmp"][0]
        self.assertEqual(
            (
                "Assets/UI/IconFont.asset",
                "Assets/UI/TmpPreset.mat",
                "Good",
                2450,
                8.0,
                ["Assets/UI/IconFont.asset::11111111111111111111111111111111"],
                False,
            ),
            (
                tmp_detail["font_asset_path"],
                tmp_detail["material_preset_path"],
                tmp_detail["material_shader"],
                tmp_detail["render_queue"],
                tmp_detail["ztest"],
                tmp_detail["atlas_references"],
                "details" in default["data"],
            ),
            msg="TMP details should surface static font/material evidence only on request",
        )

    def test_default_output_preserves_summary_rule_config_and_diagnostics(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "UI"
            _write_asset(
                project_root,
                "Assets/UI/Missing.mat",
                _material_yaml("Missing", shader_guid=None),
            )
            resolver = ReferenceResolverService(project_root=project_root)

            detailed = _response_dict(
                core_validate(resolver, scope, _rules_empty(), include_details=True)
            )
            default = _response_dict(core_validate(resolver, scope, _rules_empty()))

        self.assertEqual(
            (
                True,
                detailed["data"]["summary"],
                detailed["data"]["rule_config"],
                _material_diag_codes(detailed),
            ),
            (
                "details" not in default["data"],
                default["data"]["summary"],
                default["data"]["rule_config"],
                _material_diag_codes(default),
            ),
            msg="include_details should not change summary, rule status, or diagnostics",
        )

    def test_unavailable_optional_tmp_evidence_is_omitted_without_findings(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "UI"
            _write_shader_meta(project_root, "Assets/Shaders/Good.shader", GOOD_SHADER_GUID)
            _write_asset(
                project_root,
                "Assets/UI/TmpPreset.mat",
                _material_yaml("TmpPreset", render_queue=None, ztest=None),
                guid=TMP_MATERIAL_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/IconFont.asset",
                _font_asset_yaml(atlas_guid=None),
                guid=FONT_GUID,
            )
            _write_asset(
                project_root,
                "Assets/UI/Tmp.prefab",
                _prefab_with_renderers(
                    (TMP_MATERIAL_GUID,),
                    tmp_font_guid=FONT_GUID,
                    tmp_material_guid=TMP_MATERIAL_GUID,
                ),
            )
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(
                core_validate(resolver, scope, _rules_empty(), include_details=True)
            )

        tmp_detail = response["data"]["details"]["tmp"][0]
        self.assertEqual(
            (
                "Assets/UI/IconFont.asset",
                "Assets/UI/TmpPreset.mat",
                False,
                False,
                [],
            ),
            (
                tmp_detail["font_asset_path"],
                tmp_detail["material_preset_path"],
                "ztest" in tmp_detail,
                "atlas_references" in tmp_detail,
                response["diagnostics"],
            ),
            msg="missing optional TMP fields should be omitted without warnings",
        )

    def test_tmp_material_reference_outside_project_is_not_read(self) -> None:
        with _project() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "UI"
            _write_shader_meta(project_root, "Assets/Shaders/Good.shader", GOOD_SHADER_GUID)
            visible_material = _write_asset(
                project_root,
                "Assets/UI/Visible.mat",
                _material_yaml("Visible"),
                guid=MATCH_MATERIAL_GUID,
            )
            font_asset = _write_asset(
                project_root,
                "Assets/UI/IconFont.asset",
                _font_asset_yaml(atlas_guid=None),
                guid=FONT_GUID,
            )
            outside_mat = Path(outside_tmp) / "Escaped.mat"
            outside_mat.write_text(_material_yaml("Escaped"), encoding="utf-8")
            _write_asset(
                project_root,
                "Assets/UI/Tmp.prefab",
                _prefab_with_renderers(
                    (MATCH_MATERIAL_GUID,),
                    tmp_font_guid=FONT_GUID,
                    tmp_material_guid=TMP_MATERIAL_GUID,
                ),
            )
            resolver = ReferenceResolverService(project_root=project_root)
            cache = cast(Any, resolver)._guid_index_cache
            cache[project_root.resolve()] = {
                FONT_GUID: font_asset,
                MATCH_MATERIAL_GUID: visible_material,
                TMP_MATERIAL_GUID: outside_mat,
            }

            response = _response_dict(
                core_validate(resolver, scope, _rules_empty(), include_details=True)
            )

        tmp_detail = response["data"]["details"]["tmp"][0]
        self.assertEqual(
            (None, False, []),
            (
                tmp_detail["material_preset_path"],
                "material_shader" in tmp_detail,
                response["diagnostics"],
            ),
            msg="GUID-resolved TMP material paths outside the project must not be opened",
        )


class TestMaterialValidatorReadFailures(unittest.TestCase):
    def test_unreadable_supported_asset_returns_error_diagnostic(self) -> None:
        with _project() as tmp:
            project_root = _project_root(tmp)
            scope = project_root / "Assets" / "UI"
            bad_path = _write_binary_asset(project_root, "Assets/UI/Broken.mat", b"\xff")
            resolver = ReferenceResolverService(project_root=project_root)

            response = _response_dict(core_validate(resolver, scope, _rules_empty()))

        self.assertEqual(
            (
                False,
                "error",
                "MATERIAL_VALIDATION_READ_ERROR",
                [("MATERIAL_VALIDATION_READ_ERROR", str(bad_path.relative_to(project_root)))],
            ),
            (
                response["success"],
                response["severity"],
                response["code"],
                [
                    (diag["code"], diag["data"]["path"])
                    for diag in response["diagnostics"]
                ],
            ),
            msg="unreadable in-scope text assets should fail validation",
        )


class TestPhase1OrchestratorMaterialValidation(unittest.TestCase):
    def test_orchestrator_validate_materials_delegates_reference_resolver_and_details(self) -> None:
        expected = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="MATERIAL_VALIDATION_OK",
            message="Material validation completed.",
            data={"summary": {"scanned_files": 1}},
            diagnostics=[],
        )
        reference_resolver = MagicMock()
        orchestrator = Phase1Orchestrator(
            reference_resolver=reference_resolver,
            prefab_variant=MagicMock(),
            runtime_validation=MagicMock(),
            serialized_object=MagicMock(),
        )
        self.assertTrue(
            hasattr(orchestrator, "validate_materials"),
            "Phase1Orchestrator should expose validate_materials delegate",
        )
        self.assertTrue(
            hasattr(orchestrator_module, "orchestrator_material_validation"),
            "Phase1Orchestrator should delegate through orchestrator_material_validation import site",
        )

        with patch(
            "prefab_sentinel.orchestrator.orchestrator_material_validation.validate_materials",
            return_value=expected,
        ) as helper:
            response = orchestrator.validate_materials("Assets", include_details=True)

        self.assertIs(response, expected)
        self.assertEqual(
            (reference_resolver, "Assets", True),
            (
                helper.call_args.args[0],
                helper.call_args.args[1],
                helper.call_args.kwargs["include_details"],
            ),
        )


def _write_rules(project_root: Path, payload: dict[str, Any]) -> None:
    config_path = project_root / "config" / "material_validation_rules.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(payload), encoding="utf-8")


def _shader_rules_payload(expected_shader: str) -> dict[str, Any]:
    return {
        "version": 1,
        "shader_name_policies": [
            {
                "id": "ui-shader",
                "scope": "Assets/UI",
                "hierarchy_prefix": "Canvas/Icon",
                "expected_shader": expected_shader,
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
