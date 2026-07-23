from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any

from prefab_sentinel.benchmarking.model import BenchmarkCase, BenchmarkState

EXPECTED_CASE_IDS = (
    "inspect_wiring_cold",
    "inspect_wiring_warm",
    "inspect_hierarchy_cold",
    "inspect_hierarchy_warm",
    "inspect_material_asset_summary_cold",
    "inspect_material_asset_summary_warm",
    "validate_materials_cold",
)
EXPECTED_CARDINALITIES = {
    "game_objects": 512,
    "mono_behaviours": 256,
    "matching_mono_behaviours": 128,
    "non_matching_mono_behaviours": 128,
    "nested_instances": 16,
    "source_prefabs": 4,
    "hierarchy_depth": 8,
    "summary_material_properties": 64,
    "material_assets": 256,
    "renderer_host_assets": 64,
    "material_slot_references": 1024,
}
EXPECTED_DEPENDENCIES = ("#143", "#144", "#145", "#146", "#147", "#148", "#149", "#154")
VALID_IMPACTS = {"direct", "indirect", "non-impact"}


EXPECTED_DEPENDENCY_IMPACTS = {
    "#143": ("direct", "indirect", "direct", "indirect", "direct", "indirect", "direct"),
    "#144": ("direct",) * 7,
    "#145": ("non-impact",) * 7,
    "#146": ("non-impact",) * 7,
    "#147": ("non-impact",) * 6 + ("direct",),
    "#148": ("non-impact",) * 7,
    "#149": ("direct", "direct", "direct", "direct", "non-impact", "non-impact", "non-impact"),
    "#154": ("direct",) * 7,
}


class BenchmarkConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    schema_version: str
    fixture_version: str
    fixture_cardinalities: Mapping[str, int]
    cases: tuple[BenchmarkCase, ...]
    dependency_mapping: Mapping[str, Mapping[str, Mapping[str, str]]]

EXPECTED_SCHEMA_VERSION = "inspection-performance.v1"
EXPECTED_FIXTURE_VERSION = "synthetic-unity-project.v1"


@dataclass(frozen=True, slots=True)
class _CaseContract:
    case_id: str
    method: str
    state: str
    arguments_json: str
    measured_trials: int
    budget_sec: float


EXPECTED_CASE_CONTRACTS = (
    _CaseContract(
        "inspect_wiring_cold",
        "inspect_wiring",
        "cold",
        '{"script_filter":"BenchmarkMatch","summary_only":true,"target_path":"Assets/Benchmark/InspectionTarget.prefab"}',
        5,
        10.0,
    ),
    _CaseContract(
        "inspect_wiring_warm",
        "inspect_wiring",
        "warm",
        '{"script_filter":"BenchmarkMatch","summary_only":true,"target_path":"Assets/Benchmark/InspectionTarget.prefab"}',
        5,
        10.0,
    ),
    _CaseContract(
        "inspect_hierarchy_cold",
        "inspect_hierarchy",
        "cold",
        '{"expand_monobehaviour":true,"expand_prefab_instances":true,"show_components":true,"target_path":"Assets/Benchmark/InspectionTarget.prefab"}',
        5,
        10.0,
    ),
    _CaseContract(
        "inspect_hierarchy_warm",
        "inspect_hierarchy",
        "warm",
        '{"expand_monobehaviour":true,"expand_prefab_instances":true,"show_components":true,"target_path":"Assets/Benchmark/InspectionTarget.prefab"}',
        5,
        10.0,
    ),
    _CaseContract(
        "inspect_material_asset_summary_cold",
        "inspect_material_asset",
        "cold",
        '{"mode":"summary","property_names":["_MainTex","_Benchmark00","_Benchmark16","_Benchmark32","_Benchmark63"],"target_path":"Assets/Benchmark/Materials/Material000.mat"}',
        5,
        5.0,
    ),
    _CaseContract(
        "inspect_material_asset_summary_warm",
        "inspect_material_asset",
        "warm",
        '{"mode":"summary","property_names":["_MainTex","_Benchmark00","_Benchmark16","_Benchmark32","_Benchmark63"],"target_path":"Assets/Benchmark/Materials/Material000.mat"}',
        5,
        5.0,
    ),
    _CaseContract(
        "validate_materials_cold",
        "validate_materials",
        "cold",
        '{"include_details":false,"scope":"Assets/Benchmark"}',
        3,
        60.0,
    ),
)


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BenchmarkConfigurationError(f"{location} must be an object")
    return value


def _positive_number(value: Any, location: str) -> float:
    message = f"{location} must be a positive finite number"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise BenchmarkConfigurationError(message)
    try:
        number = float(value)
    except OverflowError as exc:
        raise BenchmarkConfigurationError(message) from exc
    if not isfinite(number):
        raise BenchmarkConfigurationError(message)
    return number


def _load_case(raw: Any, index: int) -> BenchmarkCase:
    item = _object(raw, f"cases[{index}]")
    try:
        case_id = item["id"]
        method = item["method"]
        state = item["state"]
        arguments = _object(item["arguments"], f"cases[{index}].arguments")
        measured_trials = item["measured_trials"]
        budget_sec = _positive_number(item["budget_sec"], f"cases[{index}].budget_sec")
    except KeyError as exc:
        raise BenchmarkConfigurationError(f"cases[{index}] is missing {exc.args[0]}") from exc
    if not isinstance(case_id, str) or not isinstance(method, str):
        raise BenchmarkConfigurationError(f"cases[{index}] id and method must be strings")
    if not isinstance(state, str):
        raise BenchmarkConfigurationError(f"cases[{index}].state must be cold or warm")
    case_state: BenchmarkState
    if state == "cold":
        case_state = "cold"
    elif state == "warm":
        case_state = "warm"
    else:
        raise BenchmarkConfigurationError(f"cases[{index}].state must be cold or warm")
    if not isinstance(measured_trials, int) or isinstance(measured_trials, bool):
        raise BenchmarkConfigurationError(f"cases[{index}].measured_trials must be an integer")
    return BenchmarkCase(case_id, method, case_state, arguments, measured_trials, budget_sec)


def _validate_cases(cases: tuple[BenchmarkCase, ...]) -> None:
    observed_ids = tuple(case.case_id for case in cases)
    if observed_ids != EXPECTED_CASE_IDS:
        raise BenchmarkConfigurationError(f"case inventory must equal {EXPECTED_CASE_IDS!r}")
    for case, expected in zip(cases, EXPECTED_CASE_CONTRACTS, strict=True):
        observed = _CaseContract(
            case.case_id,
            case.method,
            case.state,
            json.dumps(case.arguments, sort_keys=True, separators=(",", ":")),
            case.measured_trials,
            case.budget_sec,
        )
        if observed != expected:
            raise BenchmarkConfigurationError(
                f"{case.case_id} does not match the fixed benchmark contract"
            )


def _validate_dependencies(raw: Any) -> dict[str, dict[str, dict[str, str]]]:
    dependencies = _object(raw, "dependency_mapping")
    if tuple(dependencies) != EXPECTED_DEPENDENCIES:
        raise BenchmarkConfigurationError(
            "dependency_mapping must cover #143-#149 and #154 in order"
        )
    normalized: dict[str, dict[str, dict[str, str]]] = {}
    for issue, case_rows_raw in dependencies.items():
        case_rows = _object(case_rows_raw, f"dependency_mapping.{issue}")
        if tuple(case_rows) != EXPECTED_CASE_IDS:
            raise BenchmarkConfigurationError(
                f"dependency_mapping.{issue} must cover all seven cases"
            )
        normalized[issue] = {}
        expected_impacts = EXPECTED_DEPENDENCY_IMPACTS[issue]
        for index, (case_id, cell_raw) in enumerate(case_rows.items()):
            cell = _object(cell_raw, f"dependency_mapping.{issue}.{case_id}")
            impact = cell.get("impact")
            evidence = cell.get("evidence")
            if (
                not isinstance(impact, str)
                or impact not in VALID_IMPACTS
                or not isinstance(evidence, str)
                or not evidence.strip()
            ):
                raise BenchmarkConfigurationError(
                    f"dependency_mapping.{issue}.{case_id} requires valid impact and evidence"
                )
            if impact != expected_impacts[index]:
                raise BenchmarkConfigurationError(
                    f"dependency_mapping.{issue}.{case_id}.impact must equal "
                    f"{expected_impacts[index]!r}"
                )
            normalized[issue][case_id] = {
                "impact": impact,
                "evidence": evidence.strip(),
            }
    return normalized


def load_manifest(path: Path) -> BenchmarkManifest:
    try:
        raw = _object(json.loads(path.read_text(encoding="utf-8")), "manifest")
        fixture = _object(raw["fixture"], "fixture")
        if raw["schema_version"] != EXPECTED_SCHEMA_VERSION:
            raise BenchmarkConfigurationError(
                "schema_version does not match the fixed benchmark contract"
            )
        if fixture["version"] != EXPECTED_FIXTURE_VERSION:
            raise BenchmarkConfigurationError(
                "fixture.version does not match the fixed benchmark contract"
            )
        cardinalities = _object(fixture["cardinalities"], "fixture.cardinalities")
        cases_raw = raw["cases"]
        if not isinstance(cases_raw, list):
            raise BenchmarkConfigurationError("cases must be an array")
        cases = tuple(_load_case(item, index) for index, item in enumerate(cases_raw))
        _validate_cases(cases)
        if cardinalities != EXPECTED_CARDINALITIES:
            raise BenchmarkConfigurationError("fixture cardinalities do not match the fixed workload")
        dependencies = _validate_dependencies(raw["dependency_mapping"])
        return BenchmarkManifest(
            schema_version=EXPECTED_SCHEMA_VERSION,
            fixture_version=EXPECTED_FIXTURE_VERSION,
            fixture_cardinalities=dict(cardinalities),
            cases=cases,
            dependency_mapping=dependencies,
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkConfigurationError("manifest could not be loaded") from exc
