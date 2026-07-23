from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from prefab_sentinel.inspector_profiles import (
    ProfileRenderError,
    ProfileRepository,
    ProfileRepositoryError,
    SerializedSurface,
    SurfaceProperty,
    TargetIdentity,
    render_requested_view,
    validate_profile_against_surface,
    validate_profile_document,
)
from prefab_sentinel.inspector_profiles.application import InspectorProfileApplication
from prefab_sentinel.session import ProjectSession


def _valid_fields_profile() -> dict[str, Any]:
    return {
        "schema_version": "inspector_profile.v1",
        "target": {"managed_type": "Example.Component", "assembly": "Example.Assembly"},
        "generated_by": {
            "kind": "ai-skill",
            "skill": "prefab-sentinel:inspector-profile-authoring",
            "created_at": "2026-07-10T00:00:00Z",
            "source": "INSPECTOR_PROFILE_REQUIRED",
            "mcp_tool": "inspect_with_profile",
        },
        "confidence": "high",
        "evidence": [{"kind": "source", "detail": "Source binds exampleField."}],
        "limitations": [],
        "views": [
            {
                "name": "example",
                "kind": "fields",
                "evidence": [],
                "limitations": [],
                "fields": [{"name": "example", "path": "exampleField"}],
            }
        ],
    }


class TestInspectorProfileSchema(unittest.TestCase):
    def test_executable_profile_field_is_rejected_at_its_json_path(self) -> None:
        profile = _valid_fields_profile()
        profile["callback"] = "Dangerous.Run"

        diagnostics = validate_profile_document(profile)

        self.assertEqual(
            ("$.callback",),
            tuple(item.path for item in diagnostics),
            msg=f"closed profile schema must reject callback at $.callback; got {diagnostics!r}",
        )

    def test_all_three_declarative_view_kinds_and_authorized_metadata_validate(self) -> None:
        profile = _valid_fields_profile()
        profile["notes"] = ["Profile note."]
        profile["compatibility"] = {
            "package_name": "com.example.package",
            "package_version": "1.2.0",
            "unity_version": "2022.3",
            "notes": "Diagnostic only.",
        }
        profile["views"].extend(
            [
                {
                    "name": "arrays",
                    "kind": "zipped_arrays",
                    "evidence": [],
                    "limitations": [],
                    "notes": ["Shared-index mapping."],
                    "arrays": [
                        {"name": "first", "path": "firstArray"},
                        {"name": "second", "path": "secondArray"},
                    ],
                },
                {
                    "name": "references",
                    "kind": "object_reference_table",
                    "evidence": [],
                    "limitations": [],
                    "references": [{"name": "target", "path": "targetRef"}],
                },
            ]
        )

        diagnostics = validate_profile_document(profile)

        self.assertEqual((), diagnostics, msg=f"authority-valid profile metadata was rejected: {diagnostics!r}")


    def test_enabled_fields_writer_requires_expected_type_for_every_field(self) -> None:
        profile = _valid_fields_profile()
        profile["views"][0]["writable"] = {"enabled": True, "operations": ["set"]}

        diagnostics = validate_profile_document(profile)

        self.assertEqual(
            (("$.views[0].fields[0]", "'expected_type' is a required property"),),
            tuple((item.path, item.message) for item in diagnostics),
            msg=f"writable fields must pin the serialized type metadata: {diagnostics!r}",
        )

    def test_enabled_zipped_writer_requires_element_type_for_every_column(self) -> None:
        profile = _valid_fields_profile()
        profile["views"] = [
            {
                "name": "rows",
                "kind": "zipped_arrays",
                "evidence": [],
                "limitations": [],
                "arrays": [
                    {"name": "first", "path": "firstArray"},
                    {"name": "second", "path": "secondArray"},
                ],
                "writable": {"enabled": True, "operations": ["append_row"]},
            }
        ]

        diagnostics = validate_profile_document(profile)

        self.assertEqual(
            (
                ("$.views[0].arrays[0]", "'element_type' is a required property"),
                ("$.views[0].arrays[1]", "'element_type' is a required property"),
            ),
            tuple((item.path, item.message) for item in diagnostics),
            msg=f"writable zipped rows must pin every element type: {diagnostics!r}",
        )

    def test_medium_writer_without_persistent_approval_fails_at_writable_metadata(self) -> None:
        profile = _valid_fields_profile()
        profile["confidence"] = "medium"
        profile["views"][0]["fields"][0]["expected_type"] = "String"
        profile["views"][0]["writable"] = {"enabled": True, "operations": ["set"]}

        diagnostics = validate_profile_document(profile)

        self.assertEqual(
            ("$.views[0].writable",),
            tuple(item.path for item in diagnostics),
            msg=f"medium writer must require its persistent approval record; got {diagnostics!r}",
        )

    def test_low_confidence_view_cannot_inherit_high_profile_writer_without_approval(self) -> None:
        profile = _valid_fields_profile()
        profile["views"][0]["confidence"] = "low"
        profile["views"][0]["fields"][0]["expected_type"] = "String"
        profile["views"][0]["writable"] = {"enabled": True, "operations": ["set"]}

        diagnostics = validate_profile_document(profile)

        self.assertEqual(
            ("$.views[0].writable",),
            tuple(item.path for item in diagnostics),
            msg=f"low-confidence view must carry persistent writer approval; got {diagnostics!r}",
        )


class TestInspectorProfileDiscovery(unittest.TestCase):
    def test_bundled_profile_is_selected_when_project_has_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir, tempfile.TemporaryDirectory() as bundled_dir:
            profile = _valid_fields_profile()
            profile["target"] = {
                "managed_type": "Example.Component",
                "assembly": "Example.Assembly",
                "script_guid": "a" * 32,
                "script_file_id": 11500000,
            }
            bundled_path = Path(bundled_dir) / "generic.json"
            bundled_path.write_text(json.dumps(profile), encoding="utf-8")
            repository = ProfileRepository(Path(project_dir), Path(bundled_dir))
            identity = TargetIdentity("Example.Component", "Example.Assembly", "a" * 32, 11500000)

            selected = repository.select(identity)

            observed = (
                None
                if selected is None
                else (
                    selected.path,
                    selected.source,
                    selected.priority,
                    selected.warning,
                )
            )
            self.assertEqual(
                (bundled_path, "bundled", 0, None),
                observed,
                msg=f"bundled strong-identity fallback must be selected exactly; got {observed!r}",
            )

    def test_project_local_match_precedes_equivalent_bundled_profile(self) -> None:
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            bundled_root = root / "bundled"
            local_root = project_root / ".prefab-sentinel" / "profiles"
            local_root.mkdir(parents=True)
            bundled_root.mkdir()
            local_path = local_root / "local.json"
            bundled_path = bundled_root / "bundled.json"
            local_path.write_text(json.dumps(_valid_fields_profile()), encoding="utf-8")
            bundled_path.write_text(json.dumps(_valid_fields_profile()), encoding="utf-8")

            selected = ProfileRepository(project_root, bundled_root).select(identity)

        self.assertEqual(
            (local_path, "project", 1),
            None if selected is None else (selected.path, selected.source, selected.priority),
            msg=f"project-local profile must precede bundled fallback; got {selected!r}",
        )

    def test_same_priority_project_profiles_fail_without_nondeterministic_selection(self) -> None:
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            bundled_root = root / "bundled"
            local_root = project_root / ".prefab-sentinel" / "profiles"
            local_root.mkdir(parents=True)
            bundled_root.mkdir()
            for name in ("first.json", "second.json"):
                (local_root / name).write_text(json.dumps(_valid_fields_profile()), encoding="utf-8")

            with self.assertRaises(ProfileRepositoryError) as caught:
                ProfileRepository(project_root, bundled_root).select(identity)

        self.assertEqual(
            ("multiple profiles match at priority 1", False),
            (str(caught.exception), temporary in str(caught.exception)),
            msg=f"same-priority conflict must identify priority without host paths; got {caught.exception!r}",
        )

    def test_plausible_offline_short_type_conflict_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary) / "project"
            local_root = project_root / ".prefab-sentinel" / "profiles"
            bundled_root = Path(temporary) / "bundled"
            local_root.mkdir(parents=True)
            bundled_root.mkdir()
            for namespace in ("First", "Second"):
                profile = _valid_fields_profile()
                profile["target"] = {
                    "managed_type": f"{namespace}.Component",
                }
                (local_root / f"{namespace.lower()}.json").write_text(
                    json.dumps(profile),
                    encoding="utf-8",
                )
            identity = TargetIdentity(
                "Component",
                None,
                "a" * 32,
                11500000,
            )

            with self.assertRaises(ProfileRepositoryError) as caught:
                ProfileRepository(
                    project_root,
                    bundled_root,
                ).select_plausible_for_offline(identity)

        self.assertEqual(
            ("multiple profiles match at priority 3", False),
            (str(caught.exception), temporary in str(caught.exception)),
            msg=(
                "plausible offline matching must retain deterministic ambiguity "
                f"rejection without leaking paths: {caught.exception!r}"
            ),
        )

    def test_plausible_offline_invalid_profile_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary) / "project"
            local_root = project_root / ".prefab-sentinel" / "profiles"
            bundled_root = Path(temporary) / "bundled"
            local_root.mkdir(parents=True)
            bundled_root.mkdir()
            profile = _valid_fields_profile()
            profile["target"] = {"managed_type": "Example.Component"}
            profile["callback"] = "RunSomething"
            (local_root / "invalid.json").write_text(
                json.dumps(profile),
                encoding="utf-8",
            )
            identity = TargetIdentity(
                "Component",
                None,
                "a" * 32,
                11500000,
            )

            with self.assertRaises(ProfileRepositoryError) as caught:
                ProfileRepository(
                    project_root,
                    bundled_root,
                ).select_plausible_for_offline(identity)

        self.assertEqual(
            ("matching profile is invalid", ("$.callback",), False),
            (
                str(caught.exception),
                tuple(item.path for item in caught.exception.diagnostics),
                temporary in str(caught.exception),
            ),
            msg=(
                "plausible offline matching must retain schema fail-fast behavior "
                f"without leaking paths: {caught.exception!r}"
            ),
        )

    def test_managed_type_only_match_is_selected_with_warning(self) -> None:
        profile = _valid_fields_profile()
        profile["target"] = {"managed_type": "Example.Component"}
        identity = TargetIdentity("Example.Component", "Other.Assembly", None, None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            bundled_root = root / "bundled"
            local_root = project_root / ".prefab-sentinel" / "profiles"
            local_root.mkdir(parents=True)
            bundled_root.mkdir()
            path = local_root / "human-readable-name.json"
            path.write_text(json.dumps(profile), encoding="utf-8")

            selected = ProfileRepository(project_root, bundled_root).select(identity)

        self.assertEqual(
            (path, 2, "Profile matched by managed_type only."),
            None if selected is None else (selected.path, selected.priority, selected.warning),
            msg=f"type-only identity must remain a visible weak match; got {selected!r}",
        )

    def test_invalid_matching_local_profile_blocks_bundled_fallback(self) -> None:
        invalid_local = _valid_fields_profile()
        invalid_local["callback"] = "Run"
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            bundled_root = root / "bundled"
            local_root = project_root / ".prefab-sentinel" / "profiles"
            local_root.mkdir(parents=True)
            bundled_root.mkdir()
            (local_root / "invalid.json").write_text(json.dumps(invalid_local), encoding="utf-8")
            (bundled_root / "valid.json").write_text(json.dumps(_valid_fields_profile()), encoding="utf-8")

            with self.assertRaises(ProfileRepositoryError) as caught:
                ProfileRepository(project_root, bundled_root).select(identity)

        self.assertEqual(
            ("matching profile is invalid", ("$.callback",), False),
            (
                str(caught.exception),
                tuple(item.path for item in caught.exception.diagnostics),
                temporary in str(caught.exception),
            ),
            msg=f"matching invalid local profile must block bundled fallback; got {caught.exception!r}",
        )

    def test_unreadable_profile_discovery_error_omits_profile_path(self) -> None:
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            local_root = project_root / ".prefab-sentinel" / "profiles"
            bundled_root = root / "bundled"
            local_root.mkdir(parents=True)
            bundled_root.mkdir()
            (local_root / "invalid.json").write_bytes(b"\xff")

            with self.assertRaises(ProfileRepositoryError) as caught:
                ProfileRepository(project_root, bundled_root).select(identity)

        self.assertEqual(
            ("profile JSON could not be read", False),
            (str(caught.exception), temporary in str(caught.exception)),
            msg=f"unreadable profile discovery exposed a host path: {caught.exception!r}",
        )

    def test_human_readable_filename_cannot_override_nonmatching_target_identity(self) -> None:
        profile = _valid_fields_profile()
        profile["target"] = {"managed_type": "Other.Component", "assembly": "Other.Assembly"}
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            bundled_root = root / "bundled"
            local_root = project_root / ".prefab-sentinel" / "profiles"
            local_root.mkdir(parents=True)
            bundled_root.mkdir()
            (local_root / "Example.Component.json").write_text(json.dumps(profile), encoding="utf-8")

            selected = ProfileRepository(project_root, bundled_root).select(identity)

        self.assertIsNone(
            selected,
            msg=f"profile filename must not participate in target matching; got {selected!r}",
        )

    def test_local_and_bundled_unsafe_entries_block_selection(self) -> None:
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        for unsafe_root_name in ("local", "bundled"):
            for entry_kind in ("symlink", "directory", "fifo"):
                with self.subTest(root=unsafe_root_name, entry=entry_kind):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        project_root = root / "project"
                        local_root = project_root / ".prefab-sentinel" / "profiles"
                        bundled_root = root / "bundled"
                        local_root.mkdir(parents=True)
                        bundled_root.mkdir()
                        unsafe_root = local_root if unsafe_root_name == "local" else bundled_root
                        unsafe_entry = unsafe_root / "unsafe.json"
                        if entry_kind == "symlink":
                            target = root / "target.json"
                            target.write_text(json.dumps(_valid_fields_profile()), encoding="utf-8")
                            unsafe_entry.symlink_to(target)
                        elif entry_kind == "directory":
                            unsafe_entry.mkdir()
                        else:
                            os.mkfifo(unsafe_entry)
                        if unsafe_root_name == "local":
                            (bundled_root / "valid.json").write_text(
                                json.dumps(_valid_fields_profile()),
                                encoding="utf-8",
                            )

                        with self.assertRaises(ProfileRepositoryError) as context:
                            ProfileRepository(project_root, bundled_root).select(identity)

                    self.assertEqual(
                        ("profile path is unsafe", False),
                        (str(context.exception), temporary in str(context.exception)),
                        msg=(
                            "unsafe profile entries must stop local/bundled discovery "
                            f"without exposing host paths: {context.exception!s}"
                        ),
                    )

    def test_symlinked_discovery_roots_are_rejected(self) -> None:
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        for root_kind in ("local", "bundled"):
            with self.subTest(root=root_kind):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    project_root = root / "project"
                    project_root.mkdir()
                    outside_profiles = root / "outside" / "profiles"
                    outside_profiles.mkdir(parents=True)
                    (outside_profiles / "valid.json").write_text(
                        json.dumps(_valid_fields_profile()),
                        encoding="utf-8",
                    )
                    if root_kind == "local":
                        (project_root / ".prefab-sentinel").symlink_to(root / "outside")
                        bundled_root = root / "bundled"
                        bundled_root.mkdir()
                    else:
                        (project_root / ".prefab-sentinel" / "profiles").mkdir(parents=True)
                        bundled_root = root / "bundled"
                        bundled_root.symlink_to(outside_profiles)

                    with self.assertRaises(ProfileRepositoryError) as context:
                        ProfileRepository(project_root, bundled_root).select(identity)

                self.assertIn("profile path is unsafe", str(context.exception))

    def test_dangling_local_profile_root_blocks_bundled_fallback(self) -> None:
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            local_parent = project_root / ".prefab-sentinel"
            local_parent.mkdir(parents=True)
            (local_parent / "profiles").symlink_to(root / "missing-local")
            bundled_root = root / "bundled"
            bundled_root.mkdir()
            (bundled_root / "valid.json").write_text(
                json.dumps(_valid_fields_profile()),
                encoding="utf-8",
            )

            with self.assertRaises(ProfileRepositoryError) as context:
                ProfileRepository(project_root, bundled_root).select(identity)

        self.assertIn(
            "profile path is unsafe",
            str(context.exception),
            msg="a dangling project-local root must block bundled fallback",
        )

    def test_dangling_local_parent_blocks_bundled_fallback(self) -> None:
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            project_root.mkdir()
            (project_root / ".prefab-sentinel").symlink_to(root / "missing-parent")
            bundled_root = root / "bundled"
            bundled_root.mkdir()
            (bundled_root / "valid.json").write_text(
                json.dumps(_valid_fields_profile()),
                encoding="utf-8",
            )

            with self.assertRaises(ProfileRepositoryError) as context:
                ProfileRepository(project_root, bundled_root).select(identity)

        self.assertIn(
            "profile path is unsafe",
            str(context.exception),
            msg="a dangling parent component must block bundled fallback",
        )

    def test_dangling_bundled_profile_root_is_rejected(self) -> None:
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            project_root.mkdir()
            bundled_root = root / "bundled"
            bundled_root.symlink_to(root / "missing-bundled")

            with self.assertRaises(ProfileRepositoryError) as context:
                ProfileRepository(project_root, bundled_root).select(identity)

        self.assertIn(
            "profile path is unsafe",
            str(context.exception),
            msg="a dangling bundled root must fail closed",
        )


class TestInspectorProfileRendering(unittest.TestCase):
    def test_fields_view_returns_current_effective_value_without_unrequested_origin(self) -> None:
        profile = _valid_fields_profile()
        surface = SerializedSurface(
            target=TargetIdentity("Example.Component", "Example.Assembly", None, None),
            properties=(
                SurfaceProperty(
                    path="exampleField",
                    property_type="String",
                    source_value="base",
                    effective_value="nested-override",
                    origin={"asset_path": "Assets/Variant.prefab", "property_path": "exampleField"},
                    array_size=None,
                    element_type=None,
                ),
            ),
        )

        response = render_requested_view(
            profile,
            surface,
            "example",
            include_override_origin=False,
            writable={"enabled": False},
        )

        self.assertEqual(
            {
                "views": [
                    {
                        "name": "example",
                        "kind": "fields",
                        "fields": [
                            {
                                "name": "example",
                                "label": "example",
                                "path": "exampleField",
                                "value": "nested-override",
                            }
                        ],
                        "writable": {"enabled": False},
                    }
                ]
            },
            response,
            msg="semantic fields must use the effective value and omit origin when not requested",
        )

    def test_fields_view_preserves_groups_and_maps_known_enum_indices(self) -> None:
        profile = _valid_fields_profile()
        enum_map = {"0": "Stopped", "1": "Manual"}
        profile["views"][0]["fields"] = [
            {
                "name": "mapped",
                "path": "mappedMode",
                "group": "Playback",
                "enum_map": enum_map,
            },
            {
                "name": "unmapped",
                "path": "unmappedMode",
                "group": "Playback",
                "enum_map": enum_map,
            },
        ]
        identity = TargetIdentity(
            "Example.Component",
            "Example.Assembly",
            None,
            None,
        )
        mapped_value = {"index": 1, "name": "Manual"}
        unmapped_value = {"index": 7, "name": "FutureValue"}
        surface = SerializedSurface(
            identity,
            (
                SurfaceProperty(
                    "mappedMode",
                    "Enum",
                    mapped_value,
                    mapped_value,
                    None,
                    None,
                    None,
                ),
                SurfaceProperty(
                    "unmappedMode",
                    "Enum",
                    unmapped_value,
                    unmapped_value,
                    None,
                    None,
                    None,
                ),
            ),
        )

        response = render_requested_view(
            profile,
            surface,
            "example",
            include_override_origin=False,
            writable={"enabled": False},
        )

        fields = response["views"][0]["fields"]
        self.assertEqual(
            (
                {
                    "name": "mapped",
                    "label": "mapped",
                    "group": "Playback",
                    "enum_map": enum_map,
                    "enum_label": "Manual",
                    "path": "mappedMode",
                    "value": mapped_value,
                },
                {
                    "name": "unmapped",
                    "label": "unmapped",
                    "group": "Playback",
                    "enum_map": enum_map,
                    "enum_label": None,
                    "path": "unmappedMode",
                    "value": unmapped_value,
                },
            ),
            tuple(fields),
            msg=(
                "fields rendering must preserve declarative groups/maps, map known "
                "indices, and retain raw values when the map has no entry"
            ),
        )

    def test_zipped_view_uses_authoritative_sizes_and_ignores_stale_members(self) -> None:
        profile = _valid_fields_profile()
        profile["views"] = [
            {
                "name": "rows",
                "kind": "zipped_arrays",
                "evidence": [],
                "limitations": [],
                "arrays": [
                    {"name": "first", "path": "firstArray"},
                    {"name": "second", "path": "secondArray"},
                    {"name": "third", "path": "thirdArray"},
                ],
            }
        ]
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        properties = [
            SurfaceProperty("firstArray", "Array", None, None, None, 3, "String"),
            SurfaceProperty("secondArray", "Array", None, None, None, 2, "String"),
            SurfaceProperty("thirdArray", "Array", None, None, None, 4, "String"),
        ]
        for array_name, values in (
            ("firstArray", ("a0", "a1", "a2")),
            ("secondArray", ("b0", "b1", "stale-b2")),
            ("thirdArray", ("c0", "c1", "c2", "c3")),
        ):
            properties.extend(
                SurfaceProperty(
                    f"{array_name}.Array.data[{index}]",
                    "String",
                    f"base-{value}",
                    value,
                    None,
                    None,
                    None,
                )
                for index, value in enumerate(values)
            )
        surface = SerializedSurface(identity, tuple(properties))

        try:
            rendered = render_requested_view(
                profile,
                surface,
                "rows",
                include_override_origin=False,
                writable={"enabled": False},
            )
        except ProfileRenderError as exc:
            self.fail(f"expected two aligned zipped rows, observed ProfileRenderError: {exc}")

        self.assertEqual(
            {
                "views": [
                    {
                        "name": "rows",
                        "kind": "zipped_arrays",
                        "rows": [
                            {
                                "index": 0,
                                "fields": {
                                    "first": {"path": "firstArray.Array.data[0]", "value": "a0"},
                                    "second": {"path": "secondArray.Array.data[0]", "value": "b0"},
                                    "third": {"path": "thirdArray.Array.data[0]", "value": "c0"},
                                },
                            },
                            {
                                "index": 1,
                                "fields": {
                                    "first": {"path": "firstArray.Array.data[1]", "value": "a1"},
                                    "second": {"path": "secondArray.Array.data[1]", "value": "b1"},
                                    "third": {"path": "thirdArray.Array.data[1]", "value": "c1"},
                                },
                            },
                        ],
                        "writable": {"enabled": False},
                    }
                ]
            },
            rendered,
            msg=f"zipped rendering must stop at the shortest authoritative size; got {rendered!r}",
        )

    def test_object_reference_table_preserves_one_hop_payload_paths_and_origin(self) -> None:
        profile = _valid_fields_profile()
        profile["views"] = [
            {
                "name": "references",
                "kind": "object_reference_table",
                "evidence": [],
                "limitations": [],
                "references": [
                    {"name": "material", "label": "Material", "path": "materialRef"},
                    {"name": "target", "path": "targetRefs"},
                ],
            }
        ]
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        material = {
            "kind": "object_reference",
            "asset_path": "Assets/Material.mat",
            "file_id": 2100000,
            "missing": False,
        }
        target = {
            "kind": "object_reference",
            "asset_path": "Assets/Target.prefab",
            "file_id": 100100000,
            "missing": False,
        }
        stale = {"kind": "object_reference", "asset_path": "Assets/Stale.prefab", "file_id": 1, "missing": False}
        origin = {"layer": "nested_override", "source": "Assets/Base.prefab"}
        surface = SerializedSurface(
            identity,
            (
                SurfaceProperty("materialRef", "ObjectReference", None, material, origin, None, None),
                SurfaceProperty("targetRefs", "Array", None, None, None, 1, "PPtr<GameObject>"),
                SurfaceProperty("targetRefs.Array.data[0]", "ObjectReference", None, target, origin, None, None),
                SurfaceProperty("targetRefs.Array.data[1]", "ObjectReference", None, stale, origin, None, None),
            ),
        )

        try:
            rendered = render_requested_view(
                profile,
                surface,
                "references",
                include_override_origin=True,
                writable={"enabled": False},
            )
        except ProfileRenderError as exc:
            self.fail(f"expected scalar and array ObjectReference rows, observed ProfileRenderError: {exc}")

        self.assertEqual(
            {
                "views": [
                    {
                        "name": "references",
                        "kind": "object_reference_table",
                        "rows": [
                            {
                                "reference": "material",
                                "label": "Material",
                                "index": None,
                                "path": "materialRef",
                                "value": material,
                                "origin": origin,
                            },
                            {
                                "reference": "target",
                                "label": "target",
                                "index": 0,
                                "path": "targetRefs.Array.data[0]",
                                "value": target,
                                "origin": origin,
                            },
                        ],
                        "writable": {"enabled": False},
                    }
                ]
            },
            rendered,
            msg=f"reference table must remain one hop and honor authoritative array size; got {rendered!r}",
        )


class TestInspectorProfileWritableGates(unittest.TestCase):
    def test_high_confidence_explicit_writer_is_usable_after_all_mechanical_gates(self) -> None:
        profile = _valid_fields_profile()
        profile["views"][0]["fields"][0]["expected_type"] = "String"
        profile["views"][0]["writable"] = {"enabled": True, "operations": ["set"]}
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        surface = SerializedSurface(
            target=identity,
            properties=(SurfaceProperty("exampleField", "String", "base", "effective", None, None, None),),
        )

        result = validate_profile_against_surface(
            profile,
            identity,
            surface,
            lambda operation, paths: operation == "set" and paths == ("exampleField",),
        )

        self.assertEqual(
            (True, {"enabled": True, "operations": ["set"]}),
            (result.valid, result.writable_for("example")),
            msg=f"explicit high-confidence writer must survive every mechanical gate; got {result!r}",
        )


    def test_each_declared_operation_is_probed_over_the_complete_view_address_set(self) -> None:
        profile = _valid_fields_profile()
        profile["views"] = [
            {
                "name": "rows",
                "kind": "zipped_arrays",
                "evidence": [],
                "limitations": [],
                "arrays": [
                    {"name": "first", "path": "firstArray", "element_type": "String"},
                    {"name": "second", "path": "secondArray", "element_type": "String"},
                ],
                "writable": {
                    "enabled": True,
                    "operations": ["set_element", "append_row", "remove_row"],
                    "requires_equal_array_lengths": True,
                },
            }
        ]
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        surface = SerializedSurface(
            target=identity,
            properties=(
                SurfaceProperty("firstArray", "Array", None, None, None, 2, "String"),
                SurfaceProperty("secondArray", "Array", None, None, None, 2, "String"),
            ),
        )
        probes: list[tuple[str, tuple[str, ...]]] = []

        def probe(operation: str, paths: tuple[str, ...]) -> bool:
            probes.append((operation, paths))
            return True

        result = validate_profile_against_surface(profile, identity, surface, probe)

        self.assertEqual(
            [
                ("set_element", ("firstArray", "secondArray")),
                ("append_row", ("firstArray", "secondArray")),
                ("remove_row", ("firstArray", "secondArray")),
            ],
            probes,
            msg=f"writer validation must exercise each declared row operation: {probes!r}",
        )
        self.assertEqual(
            {"enabled": True, "operations": ["set_element", "append_row", "remove_row"], "requires_equal_array_lengths": True},
            result.writable_for("rows"),
        )


    def test_rejected_writer_probe_invalidates_writable_profile(self) -> None:
        profile = _valid_fields_profile()
        profile["views"][0]["fields"][0]["expected_type"] = "String"
        profile["views"][0]["writable"] = {"enabled": True, "operations": ["set"]}
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        surface = SerializedSurface(
            target=identity,
            properties=(SurfaceProperty("exampleField", "String", "base", "effective", None, None, None),),
        )

        result = validate_profile_against_surface(
            profile,
            identity,
            surface,
            lambda _operation, _paths: False,
        )

        self.assertEqual(
            (False, {"enabled": False}),
            (result.valid, result.writable_for("example")),
            msg=f"a rejected exact-address dry-run must invalidate writable capability: {result!r}",
        )
        self.assertEqual(
            (("$.views[0].writable", "writable path is not addressable: exampleField"),),
            tuple((item.path, item.message) for item in result.diagnostics),
            msg=f"writer rejection must identify the unaddressable path: {result.diagnostics!r}",
        )

    def test_read_only_profile_never_probes_writer(self) -> None:
        profile = _valid_fields_profile()
        profile["views"][0]["fields"][0]["expected_type"] = "String"
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        surface = SerializedSurface(
            target=identity,
            properties=(SurfaceProperty("exampleField", "String", "base", "effective", None, None, None),),
        )

        def unexpected_probe(_operation: str, _paths: tuple[str, ...]) -> bool:
            raise AssertionError("read-only profiles must not dispatch writer probes")

        result = validate_profile_against_surface(profile, identity, surface, unexpected_probe)

        self.assertEqual(
            (True, {"enabled": False}),
            (result.valid, result.writable_for("example")),
            msg=f"read-only view must stay valid without a writer probe: {result!r}",
        )


    def test_scriptable_object_writer_uses_root_asset_handle_in_real_dry_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            assets = project_root / "Assets"
            assets.mkdir()
            (assets / "Example.asset").write_text(
                "%YAML 1.1\n"
                "--- !u!114 &11400000\n"
                "MonoBehaviour:\n"
                "  exampleField: effective\n",
                encoding="utf-8",
            )
            identity = TargetIdentity(
                "Example.Settings",
                "Example.Assembly",
                None,
                None,
            )
            surface = SerializedSurface(
                target=identity,
                properties=(
                    SurfaceProperty(
                        "exampleField",
                        "String",
                        "base",
                        "effective",
                        None,
                        None,
                        None,
                    ),
                ),
                local_file_id="11400000",
            )
            session = ProjectSession(project_root)
            application = InspectorProfileApplication(session)

            addressable = application._addressability_checker(
                "Assets/Example.asset",
                None,
                surface,
            )
            result = addressable("set", ("exampleField",))

        self.assertTrue(
            result,
            msg="root ScriptableObject must pass the real $asset writer dry-run",
        )

    def test_scriptable_object_array_operations_use_real_root_asset_grammar(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            assets = project_root / "Assets"
            assets.mkdir()
            (assets / "Example.asset").write_text(
                "%YAML 1.1\n"
                "--- !u!114 &11400000\n"
                "MonoBehaviour:\n"
                "  items:\n"
                "  - first\n"
                "  - second\n",
                encoding="utf-8",
            )
            identity = TargetIdentity(
                "Example.Settings",
                "Example.Assembly",
                None,
                None,
            )
            surface = SerializedSurface(
                target=identity,
                properties=(
                    SurfaceProperty(
                        "items",
                        "Array",
                        None,
                        None,
                        None,
                        2,
                        "String",
                    ),
                    SurfaceProperty(
                        "items.Array.data[0]",
                        "String",
                        "first",
                        "first",
                        None,
                        None,
                        None,
                    ),
                    SurfaceProperty(
                        "items.Array.data[1]",
                        "String",
                        "second",
                        "second",
                        None,
                        None,
                        None,
                    ),
                ),
                local_file_id="11400000",
            )
            application = InspectorProfileApplication(ProjectSession(project_root))
            addressable = application._addressability_checker(
                "Assets/Example.asset",
                None,
                surface,
            )

            results = {
                operation: addressable(operation, ("items",))
                for operation in ("set_element", "append_row", "remove_row")
            }

        self.assertEqual(
            {
                "set_element": True,
                "append_row": True,
                "remove_row": True,
            },
            results,
            msg="every root-asset array operation must pass its actual writer grammar",
        )

    def test_prefab_component_writer_uses_exact_surface_file_id_in_real_dry_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            assets = project_root / "Assets"
            assets.mkdir()
            (assets / "Example.prefab").write_text(
                "%YAML 1.1\n"
                "--- !u!114 &3\n"
                "MonoBehaviour:\n"
                "  exampleField: effective\n",
                encoding="utf-8",
            )
            identity = TargetIdentity(
                "Example.Component",
                "Example.Assembly",
                None,
                None,
            )
            surface = SerializedSurface(
                target=identity,
                properties=(
                    SurfaceProperty(
                        "exampleField",
                        "String",
                        "base",
                        "effective",
                        None,
                        None,
                        None,
                    ),
                ),
                local_file_id="3",
            )
            session = ProjectSession(project_root)
            application = InspectorProfileApplication(session)

            addressable = application._addressability_checker(
                "Assets/Example.prefab",
                "Root/MonoBehaviour(Example)",
                surface,
            )
            result = addressable("set", ("exampleField",))

        self.assertTrue(
            result,
            msg="prefab component must pass the real exact-fileID writer dry-run",
        )

    def test_scene_component_writer_stays_disabled_without_exact_writer_grammar(
        self,
    ) -> None:
        from unittest.mock import Mock

        identity = TargetIdentity(
            "Example.Component",
            "Example.Assembly",
            None,
            None,
        )
        surface = SerializedSurface(
            target=identity,
            properties=(
                SurfaceProperty(
                    "exampleField",
                    "String",
                    "base",
                    "effective",
                    None,
                    None,
                    None,
                ),
            ),
            local_file_id="3",
        )
        session = ProjectSession(Path("/project"))
        application = InspectorProfileApplication(session)
        orchestrator = Mock()
        orchestrator.serialized_value_patch_apply.return_value = Mock(success=True)

        with patch.object(session, "get_orchestrator", return_value=orchestrator):
            addressable = application._addressability_checker(
                "Assets/Example.unity",
                "Root/MonoBehaviour(Example)",
                surface,
            )
            result = addressable("set", ("exampleField",))

        self.assertFalse(
            result,
            msg="scene writable must fail closed until its writer has an exact target grammar",
        )
        orchestrator.serialized_value_patch_apply.assert_not_called()


class TestInspectorProfileZippedArrays(unittest.TestCase):
    def test_unequal_current_lengths_are_warning_only_and_disable_writes(self) -> None:
        profile = _valid_fields_profile()
        profile["views"] = [
            {
                "name": "rows",
                "kind": "zipped_arrays",
                "evidence": [],
                "limitations": [],
                "arrays": [
                    {"name": "first", "path": "firstArray", "element_type": "String"},
                    {"name": "second", "path": "secondArray", "element_type": "String"},
                    {"name": "third", "path": "thirdArray", "element_type": "String"},
                ],
                "writable": {
                    "enabled": True,
                    "operations": ["set_element"],
                    "requires_equal_array_lengths": True,
                },
            }
        ]
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        surface = SerializedSurface(
            target=identity,
            properties=(
                SurfaceProperty("firstArray", "Array", None, None, None, 3, "String"),
                SurfaceProperty("secondArray", "Array", None, None, None, 2, "String"),
                SurfaceProperty("thirdArray", "Array", None, None, None, 4, "String"),
            ),
        )

        result = validate_profile_against_surface(profile, identity, surface, lambda _operation, _paths: True)

        self.assertEqual(
            (
                True,
                ({"view_name": "rows", "lengths": {"firstArray": 3, "secondArray": 2, "thirdArray": 4}},),
                {"enabled": False},
            ),
            (
                result.valid,
                tuple(item.to_dict() for item in result.length_mismatches),
                result.writable_for("rows"),
            ),
            msg=f"length mismatch must remain warning-only while disabling writes; got {result!r}",
        )


class TestInspectorProfileValidation(unittest.TestCase):
    def test_error_in_unrequested_view_invalidates_the_whole_profile(self) -> None:
        profile = _valid_fields_profile()
        profile["views"].append(
            {
                "name": "broken_other_view",
                "kind": "fields",
                "evidence": [],
                "limitations": [],
                "fields": [{"name": "missing", "path": "missingField"}],
            }
        )
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        surface = SerializedSurface(
            target=identity,
            properties=(SurfaceProperty("exampleField", "String", "base", "effective", None, None, None),),
        )

        result = validate_profile_against_surface(profile, identity, surface, lambda _operation, _paths: True)

        self.assertEqual(
            (False, ("$.views[1].fields[0].path",)),
            (result.valid, tuple(item.path for item in result.diagnostics)),
            msg=f"whole-profile validation must reject the unrequested missing path; got {result!r}",
        )

    def test_missing_object_reference_in_unrequested_view_invalidates_profile(self) -> None:
        profile = _valid_fields_profile()
        profile["views"].append(
            {
                "name": "references",
                "kind": "object_reference_table",
                "evidence": [],
                "limitations": [],
                "references": [{"name": "material", "path": "materialRef"}],
            }
        )
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        surface = SerializedSurface(
            target=identity,
            properties=(
                SurfaceProperty("exampleField", "String", "base", "effective", None, None, None),
                SurfaceProperty(
                    "materialRef",
                    "ObjectReference",
                    {"missing": True},
                    {"missing": True},
                    None,
                    None,
                    None,
                ),
            ),
        )

        result = validate_profile_against_surface(profile, identity, surface, lambda _operation, _paths: True)

        self.assertEqual(
            (False, ("$.views[1].references[0].path",)),
            (result.valid, tuple(item.path for item in result.diagnostics)),
            msg=f"missing ObjectReference must invalidate the whole profile; got {result!r}",
        )

    def test_fields_view_validates_resolved_and_missing_object_reference_payloads(self) -> None:
        profile = _valid_fields_profile()
        profile["views"][0]["fields"] = [{"name": "material", "path": "materialRef"}]
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)

        for missing, expected in (
            (False, (True, ())),
            (
                True,
                (
                    False,
                    (
                        (
                            "$.views[0].fields[0].path",
                            "ObjectReference is missing: materialRef",
                        ),
                    ),
                ),
            ),
        ):
            payload = {
                "object_reference": True,
                "guid": "a" * 32,
                "local_file_id": 2100000,
                "asset_path": "Assets/Material.mat",
                "object_type": "UnityEngine.Material",
                "hierarchy_path": "",
                "null": False,
                "missing": missing,
            }
            surface = SerializedSurface(
                target=identity,
                properties=(
                    SurfaceProperty(
                        "materialRef",
                        "ObjectReference",
                        payload,
                        payload,
                        None,
                        None,
                        None,
                    ),
                ),
            )

            result = validate_profile_against_surface(
                profile,
                identity,
                surface,
                lambda _operation, _paths: True,
            )

            observed = (
                result.valid,
                tuple((item.path, item.message) for item in result.diagnostics),
            )
            with self.subTest(missing=missing):
                self.assertEqual(
                    expected,
                    observed,
                    msg=f"fields ObjectReference payload validation drifted: {result!r}",
                )

    def test_zipped_view_validates_resolved_and_missing_object_reference_elements(self) -> None:
        profile = _valid_fields_profile()
        profile["views"] = [
            {
                "name": "materials",
                "kind": "zipped_arrays",
                "evidence": [],
                "limitations": [],
                "arrays": [
                    {"name": "material", "path": "materials"},
                    {"name": "mode", "path": "modes"},
                ],
            }
        ]
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)

        for missing, expected in (
            (False, (True, ())),
            (
                True,
                (
                    False,
                    (
                        (
                            "$.views[0].arrays[0].path",
                            "ObjectReference is missing: materials.Array.data[0]",
                        ),
                    ),
                ),
            ),
        ):
            payload = {
                "object_reference": True,
                "guid": "a" * 32,
                "local_file_id": 2100000,
                "asset_path": "Assets/Material.mat",
                "object_type": "UnityEngine.Material",
                "hierarchy_path": "",
                "null": False,
                "missing": missing,
            }
            surface = SerializedSurface(
                target=identity,
                properties=(
                    SurfaceProperty(
                        "materials",
                        "Generic",
                        None,
                        None,
                        None,
                        1,
                        "PPtr<Material>",
                    ),
                    SurfaceProperty(
                        "materials.Array.data[0]",
                        "ObjectReference",
                        payload,
                        payload,
                        None,
                        None,
                        None,
                    ),
                    SurfaceProperty(
                        "modes",
                        "Generic",
                        None,
                        None,
                        None,
                        1,
                        "String",
                    ),
                    SurfaceProperty(
                        "modes.Array.data[0]",
                        "String",
                        "base",
                        "default",
                        None,
                        None,
                        None,
                    ),
                ),
            )

            result = validate_profile_against_surface(
                profile,
                identity,
                surface,
                lambda _operation, _paths: True,
            )

            observed = (
                result.valid,
                tuple((item.path, item.message) for item in result.diagnostics),
            )
            with self.subTest(missing=missing):
                self.assertEqual(
                    expected,
                    observed,
                    msg=f"zipped ObjectReference element validation drifted: {result!r}",
                )


    def test_object_reference_array_accepts_unity_pptr_element_type(self) -> None:
        profile = _valid_fields_profile()
        profile["views"].append(
            {
                "name": "references",
                "kind": "object_reference_table",
                "evidence": [],
                "limitations": [],
                "references": [{"name": "material", "path": "materials"}],
            }
        )
        identity = TargetIdentity("Example.Component", "Example.Assembly", None, None)
        surface = SerializedSurface(
            target=identity,
            properties=(
                SurfaceProperty("exampleField", "String", "base", "effective", None, None, None),
                SurfaceProperty("materials", "Generic", None, None, None, 0, "PPtr<Material>"),
            ),
        )

        result = validate_profile_against_surface(profile, identity, surface, lambda _operation, _paths: True)

        self.assertEqual(
            (True, ()),
            (result.valid, result.diagnostics),
            msg=f"Unity arrayElementType PPtr<T> must be compatible with ObjectReference; got {result!r}",
        )


class TestExampleVideoCoreFixture(unittest.TestCase):
    @staticmethod
    def _profile() -> dict[str, Any]:
        return {
            "schema_version": "inspector_profile.v1",
            "target": {
                "managed_type": "Example.VideoCore",
                "assembly": "Example.Video",
                "script_guid": "c" * 32,
                "script_file_id": 11500000,
            },
            "generated_by": {
                "kind": "ai-skill",
                "skill": "prefab-sentinel:inspector-profile-authoring",
                "created_at": "2026-07-10T00:00:00Z",
                "source": "INSPECTOR_PROFILE_REQUIRED",
                "mcp_tool": "inspect_with_profile",
            },
            "confidence": "high",
            "evidence": [
                {
                    "kind": "custom_inspector",
                    "detail": "Example editor binds core playback and screen arrays.",
                }
            ],
            "limitations": [],
            "views": [
                {
                    "name": "core",
                    "kind": "fields",
                    "evidence": [],
                    "limitations": [],
                    "fields": [
                        {
                            "name": "target",
                            "label": "Playback target",
                            "path": "playbackTarget",
                            "expected_type": "ObjectReference",
                        },
                        {
                            "name": "mode",
                            "label": "Mode",
                            "path": "mode",
                            "expected_type": "Enum",
                        },
                        {
                            "name": "handler",
                            "label": "Handler",
                            "path": "handler",
                            "expected_type": "ObjectReference",
                        },
                    ],
                },
                {
                    "name": "screens",
                    "kind": "zipped_arrays",
                    "evidence": [],
                    "limitations": [],
                    "arrays": [
                        {
                            "name": "target",
                            "path": "screenTargets",
                            "element_type": "ObjectReference",
                        },
                        {
                            "name": "mode",
                            "path": "screenTargetModes",
                            "element_type": "int",
                        },
                        {
                            "name": "property",
                            "path": "screenTargetPropertyNames",
                            "element_type": "string",
                        },
                        {
                            "name": "material",
                            "path": "screenMaterials",
                            "element_type": "ObjectReference",
                        },
                    ],
                },
            ],
        }

    @staticmethod
    def _property(
        path: str,
        property_type: str,
        effective_value: Any,
        array_size: int | None = None,
        element_type: str | None = None,
    ) -> dict[str, Any]:
        return {
            "path": path,
            "property_type": property_type,
            "source_value": effective_value,
            "effective_value": effective_value,
            "origin": None,
            "array_size": array_size,
            "element_type": element_type,
        }

    @classmethod
    def _core_surface(cls, managed_type: str = "Example.VideoCore") -> dict[str, Any]:
        playback_target = {
            "object_reference": True,
            "missing": False,
            "null": False,
            "asset_path": "Assets/Media/Clip.asset",
            "guid": "d" * 32,
            "local_file_id": 11400000,
            "object_type": "Example.Clip",
            "hierarchy_path": "",
        }
        handler = {
            "object_reference": True,
            "missing": False,
            "null": False,
            "asset_path": "Assets/Video/Handler.prefab",
            "guid": "e" * 32,
            "local_file_id": 2100000,
            "object_type": "Example.VideoHandler",
            "hierarchy_path": "",
        }
        screen_targets = [
            {
                "object_reference": True,
                "missing": False,
                "null": False,
                "asset_path": f"Assets/Screens/Screen{index}.prefab",
                "guid": str(index + 1) * 32,
                "local_file_id": 1001 + index,
                "object_type": "UnityEngine.GameObject",
                "hierarchy_path": "",
            }
            for index in range(2)
        ]
        screen_target_modes = [0, 1]
        screen_target_property_names = ["_MainTex", "_EmissionMap"]
        materials = [
            {
                "object_reference": True,
                "missing": False,
                "null": False,
                "asset_path": f"Assets/Materials/Screen{index}.mat",
                "guid": chr(ord("f") - index) * 32,
                "local_file_id": 2100000,
                "object_type": "UnityEngine.Material",
                "hierarchy_path": "",
            }
            for index in range(2)
        ]
        properties = [
            cls._property("playbackTarget", "ObjectReference", playback_target),
            cls._property("mode", "Enum", {"index": 1, "name": "Loop"}),
            cls._property("handler", "ObjectReference", handler),
            cls._property("screenTargets", "Array", None, 2, "PPtr<GameObject>"),
            cls._property("screenTargetModes", "Array", None, 2, "int"),
            cls._property("screenTargetPropertyNames", "Array", None, 2, "string"),
            cls._property("screenMaterials", "Array", None, 2, "PPtr<Material>"),
            cls._property(
                "modules.Array.data[0]",
                "ObjectReference",
                {
                    "object_reference": True,
                    "missing": False,
                    "null": False,
                    "asset_path": "Assets/Video/Module.asset",
                    "guid": "9" * 32,
                    "local_file_id": 11400000,
                    "object_type": "Example.VideoModule",
                    "hierarchy_path": "",
                },
            ),
        ]
        for index in range(2):
            properties.extend(
                [
                    cls._property(
                        f"screenTargets.Array.data[{index}]",
                        "ObjectReference",
                        screen_targets[index],
                    ),
                    cls._property(
                        f"screenTargetModes.Array.data[{index}]",
                        "Integer",
                        screen_target_modes[index],
                    ),
                    cls._property(
                        f"screenTargetPropertyNames.Array.data[{index}]",
                        "String",
                        screen_target_property_names[index],
                    ),
                    cls._property(
                        f"screenMaterials.Array.data[{index}]",
                        "ObjectReference",
                        materials[index],
                    ),
                ]
            )
        return {
            "target": {
                "managed_type": managed_type,
                "assembly": "Example.Video",
                "script_guid": "c" * 32,
                "script_file_id": 11500000,
                "script_path": "Assets/Example/VideoCore.cs",
            },
            "properties": properties,
            "source_candidates_status": "complete",
            "source_candidates": [
                {
                    "kind": "runtime_script",
                    "path": "Assets/Example/VideoCore.cs",
                }
            ],
            "custom_editor_candidates": [{"type": "Example.VideoCoreEditor", "active": True}],
        }

    @staticmethod
    def _other_surface(
        managed_type: str,
        script_guid: str,
    ) -> dict[str, Any]:
        return {
            "target": {
                "managed_type": managed_type,
                "assembly": "Example.Video",
                "script_guid": script_guid,
                "script_file_id": 11500000,
                "script_path": f"Assets/Example/{managed_type.rsplit('.', 1)[-1]}.cs",
            },
            "properties": [],
            "source_candidates_status": "complete",
            "source_candidates": [],
            "custom_editor_candidates": [],
        }

    def _inspect(
        self,
        profile: dict[str, Any] | None,
        surface: dict[str, Any],
        view_name: str,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            if profile is not None:
                profile_root = project_root / ".prefab-sentinel" / "profiles"
                profile_root.mkdir(parents=True)
                (profile_root / "example-video-core.json").write_text(
                    json.dumps(profile),
                    encoding="utf-8",
                )
            application = InspectorProfileApplication(ProjectSession(project_root))
            bridge_response = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "message": "Serialized surface inspected.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }
            with (
                patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ) as mock_send,
                patch(
                    "prefab_sentinel.inspector_profiles.application.to_windows_path",
                    return_value=r"D:\UnityProject",
                ) as mock_to_windows,
            ):
                result = application.inspect_with_profile(
                    "Assets/Example.prefab",
                    view_name,
                    "Root/MonoBehaviour(Example)",
                    False,
                )
            mock_to_windows.assert_called_once_with(str(project_root))
            mock_send.assert_called_once_with(
                action="editor_inspect_serialized_surface",
                asset_path="Assets/Example.prefab",
                symbol_path="Root/MonoBehaviour(Example)",
                include_override_origin=False,
                expected_project_root=r"D:\UnityProject",
            )
        return result

    def test_handler_and_module_identities_do_not_select_the_core_profile(self) -> None:
        profile = self._profile()

        handler = self._inspect(
            profile,
            self._other_surface("Example.VideoHandler", "a" * 32),
            "core",
        )
        module = self._inspect(
            profile,
            self._other_surface("Example.VideoModule", "b" * 32),
            "core",
        )

        self.assertEqual(
            (
                (False, "INSPECTOR_PROFILE_REQUIRED"),
                (False, "INSPECTOR_PROFILE_REQUIRED"),
            ),
            (
                (handler["success"], handler["code"]),
                (module["success"], module["code"]),
            ),
            msg=f"handler or module falsely selected the Core profile: {handler!r} {module!r}",
        )

    def test_missing_core_view_is_incomplete(self) -> None:
        result = self._inspect(self._profile(), self._core_surface(), "missing")

        self.assertEqual(
            (
                False,
                "INSPECTOR_PROFILE_INCOMPLETE",
                ["core", "screens"],
            ),
            (
                result["success"],
                result["code"],
                result["data"].get("available_views"),
            ),
            msg=f"valid Core profile missing-view state drifted: {result!r}",
        )

    def test_invalid_other_core_view_blocks_requested_render(self) -> None:
        profile = self._profile()
        profile["views"].append(
            {
                "name": "broken",
                "kind": "fields",
                "evidence": [],
                "limitations": [],
                "fields": [{"name": "missing", "path": "missingField"}],
            }
        )

        result = self._inspect(profile, self._core_surface(), "core")

        diagnostic_paths = [
            diagnostic.get("path") for diagnostic in result["diagnostics"] if isinstance(diagnostic, dict)
        ]
        self.assertEqual(
            (
                False,
                "INSPECTOR_PROFILE_INVALID",
                ["$.views[2].fields[0].path"],
                None,
            ),
            (
                result["success"],
                result["code"],
                diagnostic_paths,
                result["data"].get("views"),
            ),
            msg=f"invalid Core fixture profile partially rendered: {result!r}",
        )

    def test_core_fields_pin_target_mode_handler_values_and_paths(self) -> None:
        result = self._inspect(self._profile(), self._core_surface(), "core")

        self.assertEqual(
            (
                True,
                "INSPECTOR_PROFILE_VIEW_OK",
                [
                    {
                        "name": "target",
                        "label": "Playback target",
                        "path": "playbackTarget",
                        "value": {
                            "object_reference": True,
                            "missing": False,
                            "null": False,
                            "asset_path": "Assets/Media/Clip.asset",
                            "guid": "d" * 32,
                            "local_file_id": 11400000,
                            "object_type": "Example.Clip",
                            "hierarchy_path": "",
                        },
                    },
                    {
                        "name": "mode",
                        "label": "Mode",
                        "path": "mode",
                        "value": {"index": 1, "name": "Loop"},
                    },
                    {
                        "name": "handler",
                        "label": "Handler",
                        "path": "handler",
                        "value": {
                            "object_reference": True,
                            "missing": False,
                            "null": False,
                            "asset_path": "Assets/Video/Handler.prefab",
                            "guid": "e" * 32,
                            "local_file_id": 2100000,
                            "object_type": "Example.VideoHandler",
                            "hierarchy_path": "",
                        },
                    },
                ],
            ),
            (
                result["success"],
                result["code"],
                result["data"]["views"][0]["fields"],
            ),
            msg=f"Core target/mode/handler semantic fields drifted: {result!r}",
        )

    def test_screen_target_mode_property_material_arrays_render_shared_index_rows(self) -> None:
        result = self._inspect(self._profile(), self._core_surface(), "screens")

        rows = result["data"]["views"][0]["rows"]
        observed = [
            (
                row["index"],
                row["fields"]["target"]["path"],
                row["fields"]["target"]["value"]["asset_path"],
                row["fields"]["mode"]["path"],
                row["fields"]["mode"]["value"],
                row["fields"]["property"]["path"],
                row["fields"]["property"]["value"],
                row["fields"]["material"]["path"],
                row["fields"]["material"]["value"]["asset_path"],
            )
            for row in rows
        ]
        self.assertEqual(
            [
                (
                    0,
                    "screenTargets.Array.data[0]",
                    "Assets/Screens/Screen0.prefab",
                    "screenTargetModes.Array.data[0]",
                    0,
                    "screenTargetPropertyNames.Array.data[0]",
                    "_MainTex",
                    "screenMaterials.Array.data[0]",
                    "Assets/Materials/Screen0.mat",
                ),
                (
                    1,
                    "screenTargets.Array.data[1]",
                    "Assets/Screens/Screen1.prefab",
                    "screenTargetModes.Array.data[1]",
                    1,
                    "screenTargetPropertyNames.Array.data[1]",
                    "_EmissionMap",
                    "screenMaterials.Array.data[1]",
                    "Assets/Materials/Screen1.mat",
                ),
            ],
            observed,
            msg=f"screen target/mode/property/material rows lost shared indices or raw paths: {result!r}",
        )

    def test_nested_instance_identity_still_selects_strong_core_profile(self) -> None:
        result = self._inspect(
            self._profile(),
            self._core_surface("Example.NestedVideoCore"),
            "core",
        )

        self.assertEqual(
            (True, "INSPECTOR_PROFILE_VIEW_OK", "core"),
            (
                result["success"],
                result["code"],
                result["data"]["views"][0]["name"],
            ),
            msg=f"nested strong identity did not select the Core profile: {result!r}",
        )


if __name__ == "__main__":
    unittest.main()
