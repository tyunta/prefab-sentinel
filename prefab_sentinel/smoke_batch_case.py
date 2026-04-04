from __future__ import annotations

import argparse
from pathlib import Path

from prefab_sentinel.json_io import load_json_file
from prefab_sentinel.wsl_compat import to_wsl_path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SIBLING_SAMPLE_ROOT_NAME = "UnityTool_sample"
_DEFAULT_PLAN_BY_TARGET = {
    "avatar": "avatar_prefab_create.json",
    "world": "world_material_create.json",
}


def _wsl_path_exists(p: Path) -> bool:
    """Check if *p* exists, trying WSL path conversion for Windows paths."""
    if p.exists():
        return True
    converted = to_wsl_path(str(p))
    if converted != str(p):
        return Path(converted).exists()
    return False


def _default_sample_root() -> Path:
    return _PROJECT_ROOT.parent / _SIBLING_SAMPLE_ROOT_NAME


def _default_plan_path(target: str) -> Path:
    filename = _DEFAULT_PLAN_BY_TARGET[target]
    return _PROJECT_ROOT / "config" / "bridge_smoke" / filename


def _default_project_path(target: str) -> Path:
    return _default_sample_root() / target


def _resolve_targets(raw_targets: list[str]) -> list[str]:
    expanded: list[str] = []
    for item in raw_targets:
        if item == "all":
            expanded.extend(["avatar", "world"])
        else:
            expanded.append(item)
    unique: list[str] = []
    seen: set[str] = set()
    for target in expanded:
        if target not in seen:
            seen.add(target)
            unique.append(target)
    return unique


def _build_cases(args: argparse.Namespace) -> list:  # list[SmokeCase]
    from prefab_sentinel.smoke_batch import SmokeCase

    targets = _resolve_targets(args.targets)
    cases_map: dict[str, SmokeCase] = {
        "avatar": SmokeCase(
            name="avatar",
            plan=Path(args.avatar_plan),
            project_path=Path(args.avatar_project_path),
            expect_failure=bool(args.avatar_expect_failure),
            expected_code=(
                str(args.avatar_expected_code).strip()
                if args.avatar_expected_code is not None
                else None
            ),
            expected_applied=args.avatar_expected_applied,
        ),
        "world": SmokeCase(
            name="world",
            plan=Path(args.world_plan),
            project_path=Path(args.world_project_path),
            expect_failure=bool(args.world_expect_failure),
            expected_code=(
                str(args.world_expected_code).strip()
                if args.world_expected_code is not None
                else None
            ),
            expected_applied=args.world_expected_applied,
        ),
    }
    return [cases_map[target] for target in targets]


def _load_timeout_profile_map(timeout_profile_path: Path) -> dict[str, int]:
    payload = load_json_file(timeout_profile_path)
    if not isinstance(payload, dict):
        raise ValueError("timeout profile root must be an object.")

    profiles = payload.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("timeout profile must include profiles list.")

    mapping: dict[str, int] = {}
    for item in profiles:
        if not isinstance(item, dict):
            raise ValueError("timeout profile entry must be an object.")
        target = item.get("target")
        if not isinstance(target, str) or target not in {"avatar", "world"}:
            raise ValueError("timeout profile target must be avatar/world.")
        recommended_raw = item.get("recommended_timeout_sec")
        try:
            recommended = int(recommended_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError("recommended_timeout_sec must be an integer.") from None
        if recommended <= 0:
            raise ValueError("recommended_timeout_sec must be greater than 0.")
        mapping[target] = recommended
    return mapping


def _resolve_case_unity_timeout_sec(
    *,
    case: object,  # SmokeCase
    default_timeout_sec: int | None,
    avatar_timeout_sec: int | None,
    world_timeout_sec: int | None,
    timeout_profile_overrides: dict[str, int],
) -> tuple[int | None, str]:
    per_target_overrides = {
        "avatar": avatar_timeout_sec,
        "world": world_timeout_sec,
    }
    case_name = getattr(case, "name", None)
    case_override = per_target_overrides.get(case_name)  # type: ignore[arg-type]
    if case_override is not None:
        return case_override, "target_override"
    if default_timeout_sec is not None:
        return default_timeout_sec, "default_override"
    profile_timeout = timeout_profile_overrides.get(case_name)  # type: ignore[arg-type]
    if profile_timeout is not None:
        return profile_timeout, "profile"
    return None, "none"
