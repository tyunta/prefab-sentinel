#!/usr/bin/env python3
"""Exercise every MCP tool against PF-TEST project and report results."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from prefab_sentinel.session import ProjectSession

PROJECT_ROOT = Path("/mnt/d/VRChatProject/PF-TEST")
SCOPE = str(PROJECT_ROOT / "Assets" / "Tyunta" / "SoulLinkerSystem")
PREFAB = str(PROJECT_ROOT / "Assets" / "Tyunta" / "SoulLinkerSystem" / "Prefab" / "SoulLinkSystem.prefab")
SCENE = str(PROJECT_ROOT / "Assets" / "Scenes" / "VRCDefaultWorldScene.unity")
VARIANT = str(PROJECT_ROOT / "Assets" / "Felis(Clone).prefab")
CS_FILE = str(PROJECT_ROOT / "Assets" / "Tyunta" / "SoulLinkerSystem" / "Scripts" / "StationSeatAvailability.cs")
MAT = str(PROJECT_ROOT / "Assets" / "Tyunta" / "NadeShare" / "Materials" / "PovMirror.mat")
SPHERE_PREFAB = str(PROJECT_ROOT / "Assets" / "Tyunta" / "SoulLinkerSystem" / "Prefab" / "OneSphere.prefab")

results: list[dict[str, Any]] = []


def run_tool(name: str, fn, *args, **kwargs) -> Any:
    """Run a tool function, capture result and timing."""
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        # Extract summary
        if hasattr(result, "to_dict"):
            d = result.to_dict()
        elif isinstance(result, dict):
            d = result
        elif isinstance(result, list):
            d = {"items": len(result)}
        else:
            d = {"raw": str(result)[:200]}
        success = d.get("success", True)
        code = d.get("code", "OK")
        message = d.get("message", "")[:200]
        results.append({
            "tool": name,
            "success": success,
            "code": code,
            "message": message,
            "elapsed_s": round(elapsed, 3),
        })
        return result
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        results.append({
            "tool": name,
            "success": False,
            "code": "EXCEPTION",
            "message": f"{type(exc).__name__}: {exc}"[:200],
            "elapsed_s": round(elapsed, 3),
        })
        return None


def main() -> None:
    # Create session and orchestrator
    session = ProjectSession(project_root=PROJECT_ROOT)
    orch = session.get_orchestrator()

    print("=" * 70)
    print("PF-TEST Full Tool Coverage Test")
    print("=" * 70)

    # ----------------------------------------------------------------
    # 1. get_unity_symbols (SymbolTree)
    # ----------------------------------------------------------------
    prefab_path = Path(PREFAB)
    text = prefab_path.read_text(encoding="utf-8-sig") if prefab_path.exists() else ""
    if text:
        tree = run_tool(
            "get_unity_symbols",
            session.get_symbol_tree,
            prefab_path, text, include_properties=False,
        )
        if tree:
            overview = tree.to_overview()
            print(f"\n[get_unity_symbols] {len(overview)} top-level symbols in SoulLinkSystem.prefab")

    # ----------------------------------------------------------------
    # 2. find_unity_symbol
    # ----------------------------------------------------------------
    if text:
        tree = session.get_symbol_tree(prefab_path, text, include_properties=True)
        results_find = run_tool(
            "find_unity_symbol",
            tree.query,
            "",  # empty = roots
            1,   # depth=1
        )
        if results_find:
            print(f"[find_unity_symbol] {len(results_find)} roots returned")

    # ----------------------------------------------------------------
    # 3. validate_refs
    # ----------------------------------------------------------------
    run_tool(
        "validate_refs",
        orch.validate_refs,
        SCOPE,
    )

    # ----------------------------------------------------------------
    # 4. inspect_wiring
    # ----------------------------------------------------------------
    run_tool(
        "inspect_wiring",
        orch.inspect_wiring,
        PREFAB,
    )

    # ----------------------------------------------------------------
    # 5. inspect_variant (Felis Clone)
    # ----------------------------------------------------------------
    run_tool(
        "inspect_variant",
        orch.inspect_variant,
        VARIANT,
    )

    # ----------------------------------------------------------------
    # 6. inspect_hierarchy
    # ----------------------------------------------------------------
    run_tool(
        "inspect_hierarchy",
        orch.inspect_hierarchy,
        PREFAB,
    )

    # ----------------------------------------------------------------
    # 7. inspect_materials
    # ----------------------------------------------------------------
    run_tool(
        "inspect_materials",
        orch.inspect_materials,
        PREFAB,
    )

    # ----------------------------------------------------------------
    # 8. validate_structure (inspect_structure)
    # ----------------------------------------------------------------
    run_tool(
        "validate_structure",
        orch.inspect_structure,
        PREFAB,
    )

    # ----------------------------------------------------------------
    # 9. where_used (find_referencing_assets)
    # ----------------------------------------------------------------
    run_tool(
        "find_referencing_assets",
        orch.inspect_where_used,
        SPHERE_PREFAB,
        scope=SCOPE,
    )

    # ----------------------------------------------------------------
    # 10. list_serialized_fields (by path)
    # ----------------------------------------------------------------
    run_tool(
        "list_serialized_fields (path)",
        orch.list_serialized_fields,
        CS_FILE,
    )

    # ----------------------------------------------------------------
    # 11. list_serialized_fields (by class name) — NEW
    # ----------------------------------------------------------------
    run_tool(
        "list_serialized_fields (class_name)",
        orch.list_serialized_fields,
        "StationSeatAvailability",
    )

    # ----------------------------------------------------------------
    # 12. validate_field_rename
    # ----------------------------------------------------------------
    # First get a field name
    resp = orch.list_serialized_fields(CS_FILE)
    if resp.success and resp.data.get("fields"):
        first_field = resp.data["fields"][0]["name"]
        run_tool(
            "validate_field_rename",
            orch.validate_field_rename,
            CS_FILE, first_field, first_field + "_renamed",
            scope=SCOPE,
        )
    else:
        results.append({"tool": "validate_field_rename", "success": False,
                        "code": "SKIP", "message": "No fields found", "elapsed_s": 0})

    # ----------------------------------------------------------------
    # 13. check_field_coverage
    # ----------------------------------------------------------------
    run_tool(
        "check_field_coverage",
        orch.check_field_coverage,
        SCOPE,
    )

    # ----------------------------------------------------------------
    # 14. list_overrides (service-level)
    # ----------------------------------------------------------------
    run_tool(
        "list_overrides",
        orch.prefab_variant.list_overrides,
        VARIANT,
    )

    # ----------------------------------------------------------------
    # 15. patch_apply (dry-run only — no confirm)
    # ----------------------------------------------------------------
    dry_plan = {
        "target": SPHERE_PREFAB,
        "ops": [{
            "op": "set",
            "component": "$root",
            "path": "m_LocalPosition.x",
            "value": 0.0,
        }],
    }
    run_tool(
        "patch_apply (dry-run / v1)",
        orch.patch_apply,
        dry_plan,
        confirm=False,
    )

    # ----------------------------------------------------------------
    # 16. patch_apply lenient parsing — NEW (version alias + string)
    # ----------------------------------------------------------------
    lenient_plan = {
        "version": "2",  # alias + string coercion
        "resources": [{
            "id": "sphere",
            "kind": "prefab",
            "path": SPHERE_PREFAB,
            "mode": "open",
        }],
        "ops": [{
            "op": "set",
            "resource": "sphere",
            "component": "$root",
            "path": "m_LocalPosition.y",
            "value": 1.0,
        }],
    }
    run_tool(
        "patch_apply (lenient parsing)",
        orch.patch_apply,
        lenient_plan,
        confirm=False,
    )

    # ----------------------------------------------------------------
    # 17. patch_apply bad plan — ValueError handler — NEW
    # ----------------------------------------------------------------
    bad_plan = {"plan_version": "abc", "resources": [], "ops": []}
    run_tool(
        "patch_apply (bad plan → error)",
        orch.patch_apply,
        bad_plan,
        confirm=False,
    )

    # ----------------------------------------------------------------
    # 18. revert_overrides (dry-run via patch_revert)
    # ----------------------------------------------------------------
    from prefab_sentinel.patch_revert import revert_overrides as _revert_fn
    run_tool(
        "revert_overrides (dry-run)",
        _revert_fn,
        VARIANT,
        target_file_id="0",
        property_path="m_LocalPosition",
        dry_run=True,
        confirm=False,
        change_reason="test",
    )

    # ----------------------------------------------------------------
    # 19. inspect_wiring (scene)
    # ----------------------------------------------------------------
    run_tool(
        "inspect_wiring (scene)",
        orch.inspect_wiring,
        SCENE,
    )

    # ----------------------------------------------------------------
    # 20. validate_refs (scene)
    # ----------------------------------------------------------------
    run_tool(
        "validate_refs (scene)",
        orch.validate_refs,
        SCENE,
    )

    # ----------------------------------------------------------------
    # 21. Session status
    # ----------------------------------------------------------------
    status = session.status()
    results.append({
        "tool": "session_status",
        "success": True,
        "code": "OK",
        "message": json.dumps(status, default=str)[:200],
        "elapsed_s": 0,
    })

    # ----------------------------------------------------------------
    # 22. Cache invalidation cycle
    # ----------------------------------------------------------------
    t0 = time.perf_counter()
    session.invalidate_guid_index()
    session.invalidate_script_map()
    elapsed = time.perf_counter() - t0
    results.append({
        "tool": "cache_invalidation",
        "success": True,
        "code": "OK",
        "message": "guid_index + script_map invalidated, re-warming",
        "elapsed_s": round(elapsed, 3),
    })

    # Re-warm to test caching works
    session.get_orchestrator()
    t0 = time.perf_counter()
    _ = session.script_name_map()
    elapsed = time.perf_counter() - t0
    results.append({
        "tool": "cache_rewarm (script_map)",
        "success": True,
        "code": "OK",
        "message": f"script_name_map rebuilt: {len(session.script_name_map())} entries",
        "elapsed_s": round(elapsed, 3),
    })

    # ----------------------------------------------------------------
    # 23. scope_files caching — NEW
    # ----------------------------------------------------------------
    scope_path = Path(SCOPE)
    t0 = time.perf_counter()
    files1 = orch.reference_resolver.collect_scope_files(scope_path)
    elapsed1 = time.perf_counter() - t0
    t0 = time.perf_counter()
    files2 = orch.reference_resolver.collect_scope_files(scope_path)
    elapsed2 = time.perf_counter() - t0
    results.append({
        "tool": "collect_scope_files (cold)",
        "success": True,
        "code": "OK",
        "message": f"{len(files1)} files",
        "elapsed_s": round(elapsed1, 3),
    })
    results.append({
        "tool": "collect_scope_files (cached)",
        "success": True,
        "code": "OK",
        "message": f"{len(files2)} files (same={files1==files2})",
        "elapsed_s": round(elapsed2, 3),
    })

    # ----------------------------------------------------------------
    # Print summary
    # ----------------------------------------------------------------
    # Expected failures: bad plan test, validate_refs with broken refs in scene
    expected_fail_tools = {
        "patch_apply (dry-run / v1)",   # confirm gate blocks write — expected
        "patch_apply (lenient parsing)", # confirm gate blocks write — expected
        "patch_apply (bad plan → error)",
        "validate_refs (scene)",         # broken refs exist in scene — expected
        "revert_overrides (dry-run)",    # REVERT_NO_MATCH — no matching override
    }

    print("\n" + "=" * 70)
    print(f"{'Tool':<40} {'OK?':>4} {'Code':<25} {'Time':>8}")
    print("-" * 70)
    total_time = 0.0
    pass_count = 0
    fail_count = 0
    for r in results:
        ok = "PASS" if r["success"] else "FAIL"
        if r["success"]:
            pass_count += 1
        else:
            # Some "FAIL" are expected
            if r["tool"] in expected_fail_tools or "bad plan" in r["tool"] or "error" in r["tool"]:
                pass_count += 1  # Expected failure
            else:
                fail_count += 1
        total_time += r["elapsed_s"]
        print(f"  {r['tool']:<38} {ok:>4} {r['code']:<25} {r['elapsed_s']:>7.3f}s")
    print("-" * 70)
    print(f"  Total: {len(results)} tools, {pass_count} pass, {fail_count} fail, {total_time:.3f}s")
    print()

    # Print details for failures
    # Expected failures: bad plan test, validate_refs with broken refs
    expected_fail_tools = {
        "patch_apply (dry-run / v1)",   # confirm gate blocks write — expected
        "patch_apply (lenient parsing)", # confirm gate blocks write — expected
        "patch_apply (bad plan → error)",
        "validate_refs (scene)",         # broken refs exist in scene — expected
        "revert_overrides (dry-run)",    # REVERT_NO_MATCH — no matching override
    }
    unexpected_failures = [
        r for r in results
        if not r["success"]
        and r["tool"] not in expected_fail_tools
        and "bad plan" not in r["tool"]
        and "error" not in r["tool"]
    ]
    if unexpected_failures:
        print("UNEXPECTED FAILURES:")
        for r in unexpected_failures:
            print(f"  {r['tool']}: {r['message']}")
    else:
        print("All tools working correctly.")

    # Write JSON report
    report_path = Path("/mnt/d/git/prefab-sentinel/_pf_test_results.json")
    report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nFull results: {report_path}")


if __name__ == "__main__":
    main()
