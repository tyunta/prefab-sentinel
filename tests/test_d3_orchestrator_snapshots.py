"""D3 — inspect / wiring / patch orchestrator snapshot pinning (issue #148).

The four resource-shape quadrants pinned per orchestrator are anchored on
deterministic synthetic projects:

* ``empty``    — project with a single GameObject prefab and no overrides.
* ``single``   — project with one Variant carrying a single override.
* ``multiple`` — project with one Variant carrying multiple overrides.
* ``variant``  — project with one Variant whose Base contains a nested
  ``PrefabInstance`` (the chain-resolution stress case).

Snapshots are written to
``tests/fixtures/orchestrator_inspect/expected/{inspect,wiring,patch}/{quadrant}.json``.
``--regenerate-snapshots`` overwrites the fixture in place; without the
flag the live payload must match the on-disk fixture exactly.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pytest

from prefab_sentinel.orchestrator_variant import inspect_variant
from prefab_sentinel.orchestrator_wiring import inspect_wiring
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from tests.bridge_test_helpers import write_file

FIXTURES_ROOT = (
    Path(__file__).parent / "fixtures" / "orchestrator_inspect" / "expected"
)

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"


def _write_base(root: Path) -> None:
    write_file(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
""",
    )
    write_file(
        root / "Assets" / "Base.prefab.meta",
        f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
    )


def _write_empty_project(root: Path) -> str:
    """Quadrant ``empty`` — base prefab only, no Variant."""
    _write_base(root)
    return "Assets/Base.prefab"


def _write_single_override_variant(root: Path) -> str:
    _write_base(root)
    write_file(
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: SingleOverride
      objectReference: {{fileID: 0}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
    )
    return "Assets/Variant.prefab"


def _write_multiple_overrides_variant(root: Path) -> str:
    _write_base(root)
    write_file(
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: m_Name
      value: MultiOverride
      objectReference: {{fileID: 0}}
    - target: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
      propertyPath: items.Array.size
      value: 2
      objectReference: {{fileID: 0}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
    )
    return "Assets/Variant.prefab"


def _write_nested_variant(root: Path) -> str:
    inner_guid = "abababababababababababababababab"
    write_file(
        root / "Assets" / "Inner.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Inner
""",
    )
    write_file(
        root / "Assets" / "Inner.prefab.meta",
        f"fileFormatVersion: 2\nguid: {inner_guid}\n",
    )
    write_file(
        root / "Assets" / "Base.prefab",
        f"""%YAML 1.1
--- !u!1 &200200000
GameObject:
  m_Name: Base
--- !u!1001 &300300000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {inner_guid}, type: 3}}
""",
    )
    write_file(
        root / "Assets" / "Base.prefab.meta",
        f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
    )
    write_file(
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"fileFormatVersion: 2\nguid: {VARIANT_GUID}\n",
    )
    return "Assets/Variant.prefab"


_QUADRANT_BUILDERS = {
    "empty": _write_empty_project,
    "single": _write_single_override_variant,
    "multiple": _write_multiple_overrides_variant,
    "variant": _write_nested_variant,
}


def _stable_inspect_snapshot(response) -> dict:
    """Project an ``inspect_variant`` response onto a stable snapshot."""
    step_summaries = []
    for entry in response.data["steps"]:
        result = entry["result"]
        step_summaries.append(
            {
                "step": entry["step"],
                "code": result["code"],
                "severity": result["severity"],
                "success": result["success"],
            }
        )
    return {
        "code": response.code,
        "severity": response.severity.value,
        "success": response.success,
        "fail_fast_triggered": response.data["fail_fast_triggered"],
        "steps": step_summaries,
    }


def _stable_wiring_snapshot(response) -> dict:
    return {
        "code": response.code,
        "severity": response.severity.value,
        "success": response.success,
        "data_keys": sorted(response.data.keys()),
    }


def _stable_patch_snapshot(response) -> dict:
    return {
        "code": response.code,
        "severity": response.severity.value,
        "success": response.success,
        "data_keys": sorted(response.data.keys()),
    }


def _assert_snapshot(
    sub_dir: str,
    quadrant: str,
    payload: dict,
    *,
    regenerate: bool,
) -> None:
    fixture_path = FIXTURES_ROOT / sub_dir / f"{quadrant}.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    if regenerate or not fixture_path.exists():
        fixture_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    expected = json.loads(fixture_path.read_text(encoding="utf-8"))
    if expected != payload:
        raise AssertionError(
            f"snapshot mismatch at {fixture_path}:\n"
            f"expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
            f"observed: {json.dumps(payload, indent=2, sort_keys=True)}"
        )


class TestInspectOrchestratorSnapshots:
    """``inspect_variant`` snapshots across the four quadrants."""

    @pytest.mark.parametrize("quadrant", ["empty", "single", "multiple", "variant"])
    def test_inspect_variant_quadrant(
        self, quadrant: str, regenerate_snapshots: bool
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = _QUADRANT_BUILDERS[quadrant](root)
            svc = PrefabVariantService(project_root=root)
            response = inspect_variant(svc, target)
        snapshot = _stable_inspect_snapshot(response)
        _assert_snapshot(
            "inspect", quadrant, snapshot, regenerate=regenerate_snapshots
        )


class TestWiringOrchestratorSnapshots:
    """``inspect_wiring`` snapshots across the four quadrants."""

    @pytest.mark.parametrize("quadrant", ["empty", "single", "multiple", "variant"])
    def test_inspect_wiring_quadrant(
        self, quadrant: str, regenerate_snapshots: bool
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = _QUADRANT_BUILDERS[quadrant](root)
            pv_svc = PrefabVariantService(project_root=root)
            ref_svc = ReferenceResolverService(project_root=root)
            response = inspect_wiring(pv_svc, ref_svc, target)
        snapshot = _stable_wiring_snapshot(response)
        _assert_snapshot(
            "wiring", quadrant, snapshot, regenerate=regenerate_snapshots
        )


class TestPatchOrchestratorSnapshots:
    """Patch-related snapshots — anchored on the dispatch dry-run-shape
    pinning rather than on a full ``patch_apply`` (which requires a more
    elaborate plan with confirm + change_reason).  The four quadrants
    parametrize the input prefab shape; the snapshot pins the structural
    response keys downstream consumers depend on.
    """

    @pytest.mark.parametrize("quadrant", ["empty", "single", "multiple", "variant"])
    def test_patch_dry_run_quadrant(
        self, quadrant: str, regenerate_snapshots: bool
    ) -> None:
        from prefab_sentinel.services.serialized_object import (  # noqa: PLC0415
            SerializedObjectService,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = _QUADRANT_BUILDERS[quadrant](root)
            svc = SerializedObjectService(project_root=root)
            # Empty ops triggers SER_PLAN_INVALID; non-empty ops with no
            # path triggers schema_error.  The snapshot anchor is the
            # plan_invalid envelope shape, which is deterministic across
            # quadrants.
            response = svc.dry_run_patch(target, [])
        snapshot = _stable_patch_snapshot(response)
        _assert_snapshot(
            "patch", quadrant, snapshot, regenerate=regenerate_snapshots
        )


class TestSnapshotRegenerationAnchor:
    """Single anchor row exercising the ``--regenerate-snapshots`` write
    branch (issue #148 — D3 acceptance, issue #162 — CI exercise).  The
    regeneration branch must produce a fresh fixture file from the live
    orchestrator payload.

    Why the test routes through the production ``_assert_snapshot``
    helper rather than emulating the write in-line: emulating the write
    bypasses the very branch the test is meant to cover, so a mutation
    inside the helper's ``regenerate or not fixture_path.exists()``
    arm would survive the suite.  The anchor here patches
    ``FIXTURES_ROOT`` onto a temporary directory so the helper's real
    write executes against a private path without racing the shared
    snapshot files.
    """

    def test_regeneration_overwrites_stale_fixture_with_live_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pre-write a stale fixture under the temporary fixture root so
        # the regenerate branch's *overwrite* behaviour is observable
        # (the ``not fixture_path.exists()`` arm is a separate path).
        sub_dir = "inspect"
        quadrant = "single"
        stale_path = tmp_path / sub_dir / f"{quadrant}.json"
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text(
            json.dumps({"code": "STALE"}) + "\n", encoding="utf-8"
        )

        monkeypatch.setattr(
            "tests.test_d3_orchestrator_snapshots.FIXTURES_ROOT", tmp_path
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = _write_single_override_variant(root)
            svc = PrefabVariantService(project_root=root)
            response = inspect_variant(svc, target)
        live_payload = _stable_inspect_snapshot(response)

        _assert_snapshot(sub_dir, quadrant, live_payload, regenerate=True)

        on_disk = json.loads(stale_path.read_text(encoding="utf-8"))
        assert on_disk == live_payload, (
            f"regenerate branch did not overwrite the stale fixture: "
            f"on_disk={on_disk!r} live_payload={live_payload!r}"
        )

    def test_regeneration_creates_missing_fixture_with_live_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The ``not fixture_path.exists()`` arm of the helper writes the
        # fixture even when ``regenerate=False``.  Anchoring this arm
        # ensures the test runner does not silently fall through to the
        # equality assertion when a fixture has gone missing.
        monkeypatch.setattr(
            "tests.test_d3_orchestrator_snapshots.FIXTURES_ROOT", tmp_path
        )
        sub_dir = "wiring"
        quadrant = "empty"
        fixture_path = tmp_path / sub_dir / f"{quadrant}.json"
        assert not fixture_path.exists()

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = _write_empty_project(root)
            pv_svc = PrefabVariantService(project_root=root)
            ref_svc = ReferenceResolverService(project_root=root)
            response = inspect_wiring(pv_svc, ref_svc, target)
        live_payload = _stable_wiring_snapshot(response)

        _assert_snapshot(sub_dir, quadrant, live_payload, regenerate=False)

        on_disk = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert on_disk == live_payload


if __name__ == "__main__":
    unittest.main()
