from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.udon_wiring_parser import UDON_BEHAVIOUR_GUID
from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_transform


SOURCE_GUID = "44444444444444444444444444444444"
HOST_GUID = "55555555555555555555555555555555"
BUTTON_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SLIDER_GUID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
TOGGLE_GUID = "cccccccccccccccccccccccccccccccc"
RECEIVER_GUID = "dddddddddddddddddddddddddddddddd"
UDON_PROXY_GUID = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"


def _load_inspector() -> Any:
    try:
        from prefab_sentinel.unity_event_listener_inspector import (
            inspect_unity_event_listeners,
        )
    except ImportError as exc:
        raise AssertionError(
            "expected inspect_unity_event_listeners to return structured "
            f"UnityEvent listener entries; observed missing import: {exc}"
        ) from exc
    return inspect_unity_event_listeners


def _write_prefab(root: Path, name: str, guid: str, text: str) -> Path:
    assets = root / "Assets"
    assets.mkdir(parents=True, exist_ok=True)
    prefab_path = assets / name
    prefab_path.write_text(text, encoding="utf-8")
    prefab_path.with_suffix(prefab_path.suffix + ".meta").write_text(
        f"fileFormatVersion: 2\nguid: {guid}\n",
        encoding="utf-8",
    )
    return prefab_path


def _ui_component(
    file_id: str,
    game_object_file_id: str,
    component_type: str,
    event_field: str = "",
) -> str:
    guid_by_type = {
        "Button": BUTTON_GUID,
        "Slider": SLIDER_GUID,
        "Toggle": TOGGLE_GUID,
    }
    return (
        f"--- !u!114 &{file_id}\n"
        "MonoBehaviour:\n"
        f"  m_GameObject: {{fileID: {game_object_file_id}}}\n"
        f"  m_Script: {{fileID: 11500000, guid: {guid_by_type[component_type]}, type: 3}}\n"
        f"  m_EditorClassIdentifier: UnityEngine.UI.{component_type}\n"
        f"{event_field}"
    )


def _receiver_component(
    file_id: str,
    game_object_file_id: str,
    class_name: str = "Receiver",
) -> str:
    return (
        f"--- !u!114 &{file_id}\n"
        "MonoBehaviour:\n"
        f"  m_GameObject: {{fileID: {game_object_file_id}}}\n"
        f"  m_Script: {{fileID: 11500000, guid: {RECEIVER_GUID}, type: 3}}\n"
        f"  m_EditorClassIdentifier: Assembly-CSharp::{class_name}\n"
    )


def _udon_proxy_component(
    file_id: str,
    game_object_file_id: str,
    *,
    backing_file_id: str = "600",
) -> str:
    return (
        f"--- !u!114 &{file_id}\n"
        "MonoBehaviour:\n"
        f"  m_GameObject: {{fileID: {game_object_file_id}}}\n"
        f"  m_Script: {{fileID: 11500000, guid: {UDON_PROXY_GUID}, type: 3}}\n"
        "  m_EditorClassIdentifier: Assembly-CSharp::ExampleUdonProxy\n"
        f"  _udonSharpBackingUdonBehaviour: {{fileID: {backing_file_id}}}\n"
    )


def _udon_behaviour_component(file_id: str, game_object_file_id: str) -> str:
    return (
        f"--- !u!114 &{file_id}\n"
        "MonoBehaviour:\n"
        f"  m_GameObject: {{fileID: {game_object_file_id}}}\n"
        f"  m_Script: {{fileID: 11500000, guid: {UDON_BEHAVIOUR_GUID}, type: 3}}\n"
        "  m_EditorClassIdentifier: VRC.Udon.UdonBehaviour\n"
    )


def _event_field(
    field_name: str,
    *,
    target_file_id: str,
    method: str,
    mode: int = 5,
    string_arg: str = "payload",
    call_state: int = 2,
) -> str:
    return (
        f"  {field_name}:\n"
        "    m_PersistentCalls:\n"
        "      m_Calls:\n"
        f"      - m_Target: {{fileID: {target_file_id}}}\n"
        "        m_TargetAssemblyTypeName: Assembly-CSharp.Receiver, Assembly-CSharp\n"
        f"        m_MethodName: {method}\n"
        f"        m_Mode: {mode}\n"
        "        m_Arguments:\n"
        "          m_ObjectArgument: {fileID: 0}\n"
        "          m_ObjectArgumentAssemblyTypeName: UnityEngine.Object, UnityEngine\n"
        "          m_IntArgument: 0\n"
        "          m_FloatArgument: 0\n"
        f"          m_StringArgument: {string_arg}\n"
        "          m_BoolArgument: 0\n"
        f"        m_CallState: {call_state}\n"
    )


def _empty_event_field(field_name: str) -> str:
    return (
        f"  {field_name}:\n"
        "    m_PersistentCalls:\n"
        "      m_Calls: []\n"
    )


def _control_prefab(
    component_type: str,
    field_name: str,
    *,
    event_field: str | None,
    target_component: str | None = None,
    extra_target_components: str = "",
) -> str:
    target_ids = ["201"]
    if target_component is not None:
        target_ids.append("500")
    if extra_target_components:
        target_ids.append("600")
    return (
        YAML_HEADER
        + make_gameobject("100", "Control", ["200", "300"])
        + make_transform("200", "100", children_file_ids=["201"])
        + make_gameobject("101", "Target", target_ids)
        + make_transform("201", "101", father_file_id="200")
        + _ui_component("300", "100", component_type, event_field or "")
        + (target_component or "")
        + extra_target_components
    )


def _source_prefab_with_button(event_field: str) -> str:
    return _control_prefab(
        "Button",
        "m_OnClick",
        event_field=event_field,
        target_component=_receiver_component("500", "101"),
    )


def _prefab_instance(
    instance_file_id: str,
    source_guid: str,
    *,
    parent_transform: str = "2000",
    modifications: list[tuple[str, str, str, str]] | None = None,
) -> str:
    if modifications:
        modification_lines = "\n".join(
            "\n".join(
                [
                    f"    - target: {{fileID: {target_file_id}, guid: {source_guid}, type: 3}}",
                    f"      propertyPath: {property_path}",
                    f"      value: {value}",
                    f"      objectReference: {object_reference}",
                ]
            )
            for target_file_id, property_path, value, object_reference in modifications
        )
        modifications_block = f"    m_Modifications:\n{modification_lines}\n"
    else:
        modifications_block = "    m_Modifications: []\n"
    return (
        f"--- !u!1001 &{instance_file_id}\n"
        "PrefabInstance:\n"
        "  m_Modification:\n"
        f"    m_TransformParent: {{fileID: {parent_transform}}}\n"
        f"{modifications_block}"
        f"  m_SourcePrefab: {{fileID: 100100000, guid: {source_guid}, type: 3}}\n"
    )


def _host_prefab(instance_text: str) -> str:
    return (
        YAML_HEADER
        + make_gameobject("1000", "HostRoot", ["2000"])
        + make_transform("2000", "1000")
        + instance_text
    )


class UnityEventListenerSurfaceTests(unittest.TestCase):
    def test_supported_button_slider_and_toggle_listeners_return_structured_entries(self) -> None:
        cases = [
            ("Button", "onClick", "m_OnClick", "ButtonClicked"),
            ("Slider", "onValueChanged", "m_OnValueChanged", "SliderChanged"),
            ("Toggle", "onValueChanged", "m_OnValueChanged", "ToggleChanged"),
        ]
        observed_entries: list[tuple[str, str, str, str, int, str, int]] = []
        for component_type, property_name, field_name, method in cases:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_prefab(
                    root,
                    "Control.prefab",
                    SOURCE_GUID,
                    _control_prefab(
                        component_type,
                        field_name,
                        event_field=_event_field(
                            field_name,
                            target_file_id="500",
                            method=method,
                        ),
                        target_component=_receiver_component("500", "101"),
                    ),
                )
                inspect = _load_inspector()

                response = inspect(
                    PrefabVariantService(root),
                    "Assets/Control.prefab",
                    "Control",
                    component_type,
                    property_name,
                ).to_dict()

            entry = response["data"]["listeners"][0]
            observed_entries.append(
                (
                    component_type,
                    response["code"],
                    entry["target_object_path"],
                    entry["target_component_type"],
                    entry["persistent_listener_mode"],
                    entry["argument"]["string"],
                    entry["call_state"],
                )
            )
        self.assertEqual(
            [
                ("Button", "INSPECT_UNITY_EVENT_LISTENERS", "Control/Target", "Receiver", 5, "payload", 2),
                ("Slider", "INSPECT_UNITY_EVENT_LISTENERS", "Control/Target", "Receiver", 5, "payload", 2),
                ("Toggle", "INSPECT_UNITY_EVENT_LISTENERS", "Control/Target", "Receiver", 5, "payload", 2),
            ],
            observed_entries,
            msg=f"supported uGUI events should return structured persistent listeners; observed entries={observed_entries!r}",
        )


class UnityEventListenerOriginTests(unittest.TestCase):
    def test_nested_prefab_listener_override_distinguishes_source_override_and_effective_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(
                root,
                "Nested.prefab",
                SOURCE_GUID,
                _source_prefab_with_button(_empty_event_field("m_OnClick")),
            )
            _write_prefab(
                root,
                "Host.prefab",
                HOST_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9000",
                        SOURCE_GUID,
                        modifications=[
                            ("300", "m_OnClick.m_PersistentCalls.m_Calls.Array.size", "1", "{fileID: 0}"),
                            ("300", "m_OnClick.m_PersistentCalls.m_Calls.Array.data[0].m_Target", "", "{fileID: 500}"),
                            ("300", "m_OnClick.m_PersistentCalls.m_Calls.Array.data[0].m_MethodName", "HostOverride", "{fileID: 0}"),
                            ("300", "m_OnClick.m_PersistentCalls.m_Calls.Array.data[0].m_Mode", "5", "{fileID: 0}"),
                            ("300", "m_OnClick.m_PersistentCalls.m_Calls.Array.data[0].m_Arguments.m_StringArgument", "FromHost", "{fileID: 0}"),
                            ("300", "m_OnClick.m_PersistentCalls.m_Calls.Array.data[0].m_CallState", "2", "{fileID: 0}"),
                        ],
                    )
                ),
            )
            inspect = _load_inspector()

            response = inspect(
                PrefabVariantService(root),
                "Assets/Host.prefab",
                "HostRoot/Control",
                "Button",
                "onClick",
            ).to_dict()

        entry = response["data"]["listeners"][0]
        self.assertEqual(
            (
                True,
                0,
                1,
                "HostOverride",
                "FromHost",
                "host_override",
                "Assets/Nested.prefab",
                "Assets/Host.prefab",
                "HostRoot/Control/Target",
            ),
            (
                response["success"],
                response["data"]["source_listener_count"],
                response["data"]["effective_listener_count"],
                entry["method"],
                entry["argument"]["string"],
                entry["origin"]["kind"],
                entry["origin"]["source"]["asset_path"],
                entry["origin"]["override_host"]["asset_path"],
                entry["target_object_path"],
            ),
            msg=f"nested listener override should distinguish source default, host override, and effective listener; observed response={response!r}",
        )


    def test_array_size_override_shrinks_and_host_added_empty_slot_has_override_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(
                root,
                "ShrinkNested.prefab",
                SOURCE_GUID,
                _source_prefab_with_button(
                    _event_field("m_OnClick", target_file_id="500", method="SourceListener")
                ),
            )
            _write_prefab(
                root,
                "ShrinkHost.prefab",
                HOST_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9000",
                        SOURCE_GUID,
                        modifications=[
                            ("300", "m_OnClick.m_PersistentCalls.m_Calls.Array.size", "0", "{fileID: 0}"),
                            ("300", "m_OnClick.m_PersistentCalls.m_Calls.Array.data[1000].m_MethodName", "Stale", "{fileID: 0}"),
                        ],
                    )
                ),
            )
            _write_prefab(
                root,
                "GrowNested.prefab",
                BUTTON_GUID,
                _source_prefab_with_button(_empty_event_field("m_OnClick")),
            )
            _write_prefab(
                root,
                "GrowHost.prefab",
                SLIDER_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9100",
                        BUTTON_GUID,
                        modifications=[
                            ("300", "m_OnClick.m_PersistentCalls.m_Calls.Array.size", "1", "{fileID: 0}"),
                        ],
                    )
                ),
            )
            _write_prefab(
                root,
                "SparseNested.prefab",
                TOGGLE_GUID,
                _source_prefab_with_button(_empty_event_field("m_OnClick")),
            )
            _write_prefab(
                root,
                "SparseHost.prefab",
                RECEIVER_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9200",
                        TOGGLE_GUID,
                        modifications=[
                            ("300", "m_OnClick.m_PersistentCalls.m_Calls.Array.size", "1000", "{fileID: 0}"),
                        ],
                    )
                ),
            )
            inspect = _load_inspector()

            shrink = inspect(
                PrefabVariantService(root),
                "Assets/ShrinkHost.prefab",
                "HostRoot/Control",
                "Button",
                "onClick",
            ).to_dict()
            grow = inspect(
                PrefabVariantService(root),
                "Assets/GrowHost.prefab",
                "HostRoot/Control",
                "Button",
                "onClick",
            ).to_dict()
            sparse = inspect(
                PrefabVariantService(root),
                "Assets/SparseHost.prefab",
                "HostRoot/Control",
                "Button",
                "onClick",
            ).to_dict()

        grown_entry = grow["data"]["listeners"][0]
        self.assertEqual(
            (
                True,
                1,
                0,
                True,
                0,
                1,
                "host_override",
                ["Array.size"],
                False,
                "INSPECT_UNITY_EVENT_LISTENER_BOUNDS_ERROR",
            ),
            (
                shrink["success"],
                shrink["data"]["source_listener_count"],
                shrink["data"]["effective_listener_count"],
                grow["success"],
                grow["data"]["source_listener_count"],
                grow["data"]["effective_listener_count"],
                grown_entry["origin"]["kind"],
                grown_entry["origin"]["overridden_fields"],
                sparse["success"],
                sparse["code"],
            ),
            msg=(
                "Array.size overrides must remain authoritative, ignore stale "
                f"out-of-range per-index overrides, and reject sparse growth; shrink={shrink!r} grow={grow!r} sparse={sparse!r}"
            ),
        )

    def test_repeated_nested_prefab_instances_resolve_listener_targets_with_instance_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(
                root,
                "Nested.prefab",
                SOURCE_GUID,
                _source_prefab_with_button(
                    _event_field("m_OnClick", target_file_id="500", method="SourceListener")
                ),
            )
            _write_prefab(
                root,
                "Host.prefab",
                HOST_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9000",
                        SOURCE_GUID,
                        modifications=[("100", "m_Name", "FirstControl", "{fileID: 0}")],
                    )
                    + _prefab_instance(
                        "9001",
                        SOURCE_GUID,
                        modifications=[("100", "m_Name", "SecondControl", "{fileID: 0}")],
                    )
                ),
            )
            inspect = _load_inspector()

            response = inspect(
                PrefabVariantService(root),
                "Assets/Host.prefab",
                "HostRoot/FirstControl",
                "Button",
                "onClick",
            ).to_dict()

        self.assertEqual(
            (
                True,
                "HostRoot/FirstControl/Target",
                "Receiver",
                [],
            ),
            (
                response["success"],
                response["data"]["listeners"][0]["target_object_path"],
                response["data"]["listeners"][0]["target_component_type"],
                [diag["code"] for diag in response["diagnostics"]],
            ),
            msg=f"listener target lookup must stay inside the selected nested prefab instance: {response!r}",
        )

    def test_deep_repeated_nested_prefab_instances_resolve_listener_targets_by_full_ancestry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(
                root,
                "Inner.prefab",
                BUTTON_GUID,
                _source_prefab_with_button(
                    _event_field("m_OnClick", target_file_id="500", method="InnerListener")
                ),
            )
            outer = (
                YAML_HEADER
                + make_gameobject("700", "Outer", ["800"])
                + make_transform("800", "700")
                + _prefab_instance("8100", BUTTON_GUID, parent_transform="800")
            )
            _write_prefab(root, "Outer.prefab", SOURCE_GUID, outer)
            _write_prefab(
                root,
                "Host.prefab",
                HOST_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9000",
                        SOURCE_GUID,
                        modifications=[("700", "m_Name", "FirstOuter", "{fileID: 0}")],
                    )
                    + _prefab_instance(
                        "9001",
                        SOURCE_GUID,
                        modifications=[("700", "m_Name", "SecondOuter", "{fileID: 0}")],
                    )
                ),
            )
            inspect = _load_inspector()

            response = inspect(
                PrefabVariantService(root),
                "Assets/Host.prefab",
                "HostRoot/FirstOuter/Control",
                "Button",
                "onClick",
            ).to_dict()

        self.assertEqual(
            (
                True,
                "HostRoot/FirstOuter/Control/Target",
                "Receiver",
                [],
            ),
            (
                response["success"],
                response["data"]["listeners"][0]["target_object_path"],
                response["data"]["listeners"][0]["target_component_type"],
                [diag["code"] for diag in response["diagnostics"]],
            ),
            msg=f"deep listener target lookup must stay inside the full selected ancestry: {response!r}",
        )

class UnityEventUdonSharpDiagnosticTests(unittest.TestCase):
    def test_udonsharp_proxy_warning_requires_backing_udon_sendcustom_event_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(
                root,
                "ProxyOnly.prefab",
                SOURCE_GUID,
                _control_prefab(
                    "Button",
                    "m_OnClick",
                    event_field=_event_field("m_OnClick", target_file_id="500", method="ProxyMethod"),
                    target_component=_udon_proxy_component("500", "101"),
                ),
            )
            _write_prefab(
                root,
                "KnownBad.prefab",
                HOST_GUID,
                _control_prefab(
                    "Button",
                    "m_OnClick",
                    event_field=_event_field("m_OnClick", target_file_id="500", method="ProxyMethod"),
                    target_component=_udon_proxy_component("500", "101", backing_file_id="600"),
                    extra_target_components=_udon_behaviour_component("600", "101"),
                ),
            )
            inspect = _load_inspector()

            proxy_only = inspect(
                PrefabVariantService(root),
                "Assets/ProxyOnly.prefab",
                "Control",
                "Button",
                "onClick",
            ).to_dict()
            known_bad = inspect(
                PrefabVariantService(root),
                "Assets/KnownBad.prefab",
                "Control",
                "Button",
                "onClick",
            ).to_dict()

        self.assertEqual(
            (
                True,
                "info",
                [],
                True,
                "warning",
                ["INSPECT_UNITY_EVENT_UDONSHARP_PROXY_TARGET"],
            ),
            (
                proxy_only["success"],
                proxy_only["severity"],
                [diag["code"] for diag in proxy_only["diagnostics"]],
                known_bad["success"],
                known_bad["severity"],
                [diag["code"] for diag in known_bad["diagnostics"]],
            ),
            msg=f"UdonSharp proxy-only evidence must not warn, but proxy plus backing UdonBehaviour should warn; proxy_only={proxy_only!r} known_bad={known_bad!r}",
        )


class UnityEventListenerErrorTests(unittest.TestCase):
    def test_unsupported_component_property_pair_lists_supported_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(root, "Control.prefab", SOURCE_GUID, _source_prefab_with_button(_empty_event_field("m_OnClick")))
            inspect = _load_inspector()

            response = inspect(
                PrefabVariantService(root),
                "Assets/Control.prefab",
                "Control",
                "Text",
                "onClick",
            ).to_dict()

        self.assertEqual(
            (
                False,
                "INSPECT_UNITY_EVENT_UNSUPPORTED_SURFACE",
                True,
                True,
                True,
                True,
            ),
            (
                response["success"],
                response["code"],
                response["data"]["read_only"],
                "Button.onClick" in response["message"],
                "Slider.onValueChanged" in response["message"],
                "Toggle.onValueChanged" in response["message"],
            ),
            msg=f"unsupported selector should be typed and list supported pairs; observed response={response!r}",
        )

    def test_missing_object_component_and_event_field_have_distinct_error_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(
                root,
                "NoComponent.prefab",
                SOURCE_GUID,
                _control_prefab("Button", "m_OnClick", event_field=None, target_component=None),
            )
            _write_prefab(
                root,
                "NoField.prefab",
                HOST_GUID,
                _control_prefab("Button", "m_OnClick", event_field=None, target_component=_receiver_component("500", "101")),
            )
            inspect = _load_inspector()

            missing_object = inspect(
                PrefabVariantService(root),
                "Assets/NoComponent.prefab",
                "Missing",
                "Button",
                "onClick",
            ).to_dict()
            missing_component = inspect(
                PrefabVariantService(root),
                "Assets/NoComponent.prefab",
                "Control",
                "Slider",
                "onValueChanged",
            ).to_dict()
            missing_field = inspect(
                PrefabVariantService(root),
                "Assets/NoField.prefab",
                "Control",
                "Button",
                "onClick",
            ).to_dict()

        self.assertEqual(
            (
                "INSPECT_UNITY_EVENT_OBJECT_NOT_FOUND",
                "INSPECT_UNITY_EVENT_COMPONENT_NOT_FOUND",
                "INSPECT_UNITY_EVENT_FIELD_NOT_FOUND",
                True,
                "Button",
                "onClick",
            ),
            (
                missing_object["code"],
                missing_component["code"],
                missing_field["code"],
                missing_field["data"]["read_only"],
                missing_field["data"]["component_type"],
                missing_field["data"]["property_name"],
            ),
            msg=(
                "missing object, missing component, and missing event field should "
                f"stay distinct; object={missing_object!r} component={missing_component!r} field={missing_field!r}"
            ),
        )

    def test_malformed_numeric_listener_field_returns_typed_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            malformed_event = _event_field(
                "m_OnClick", target_file_id="500", method="BrokenNumeric"
            ).replace("m_Mode: 5", "m_Mode: not-an-int")
            _write_prefab(
                root,
                "Broken.prefab",
                SOURCE_GUID,
                _source_prefab_with_button(malformed_event),
            )
            inspect = _load_inspector()

            response = inspect(
                PrefabVariantService(root),
                "Assets/Broken.prefab",
                "Control",
                "Button",
                "onClick",
            ).to_dict()

        self.assertEqual(
            (
                False,
                "INSPECT_UNITY_EVENT_NUMERIC_PARSE_ERROR",
                "error",
                ["INSPECT_UNITY_EVENT_NUMERIC_PARSE_ERROR"],
            ),
            (
                response["success"],
                response["code"],
                response["severity"],
                [diag["code"] for diag in response["diagnostics"]],
            ),
            msg=f"malformed UnityEvent numeric fields must return a typed error instead of crashing or defaulting: {response!r}",
        )

    def test_missing_or_undecodable_asset_returns_typed_read_only_error_without_listeners(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root / "Assets"
            assets.mkdir(parents=True, exist_ok=True)
            (assets / "Bad.prefab").write_bytes(b"\xff\xfe\xfa")
            inspect = _load_inspector()

            missing = inspect(
                PrefabVariantService(root),
                "Assets/Missing.prefab",
                "Control",
                "Button",
                "onClick",
            ).to_dict()
            read_error = inspect(
                PrefabVariantService(root),
                "Assets/Bad.prefab",
                "Control",
                "Button",
                "onClick",
            ).to_dict()

        self.assertEqual(
            (
                False,
                "INSPECT_UNITY_EVENT_FILE_NOT_FOUND",
                True,
                False,
                False,
                "INSPECT_UNITY_EVENT_READ_ERROR",
                True,
                False,
            ),
            (
                missing["success"],
                missing["code"],
                missing["data"]["read_only"],
                "listeners" in missing["data"],
                read_error["success"],
                read_error["code"],
                read_error["data"]["read_only"],
                "listeners" in read_error["data"],
            ),
            msg=f"UnityEvent read failures should be typed read-only errors without listeners; missing={missing!r} read_error={read_error!r}",
        )
