from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.nested_prefab_cache import NestedPrefabCache
from prefab_sentinel.unity_assets import decode_text_file
from prefab_sentinel.unity_yaml_parser import split_yaml_blocks

GUID_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
GUID_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
GUID_C = "cccccccccccccccccccccccccccccccc"
MISSING_GUID = "dddddddddddddddddddddddddddddddd"


def _prefab_instance(guid: str, file_id: str) -> str:
    return (
        f"--- !u!1001 &{file_id}\n"
        "PrefabInstance:\n"
        "  m_Modification:\n"
        "    m_TransformParent: {fileID: 0}\n"
        f"  m_SourcePrefab: {{fileID: 100100000, guid: {guid}, type: 3}}\n"
    )


def _host_text(*guids: str) -> str:
    return "%YAML 1.1\n" + "".join(
        _prefab_instance(guid, str(1000 + index))
        for index, guid in enumerate(guids)
    )


def _child_text(name: str) -> str:
    return (
        "%YAML 1.1\n"
        "--- !u!1 &100\n"
        "GameObject:\n"
        f"  m_Name: {name}\n"
    )


class NestedPrefabCacheTests(unittest.TestCase):
    def test_prefetch_reads_duplicate_source_once_and_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project_root = Path(raw)
            child = project_root / "Assets" / "Child.prefab"
            child.parent.mkdir(parents=True)
            child.write_text(_child_text("Child"), encoding="utf-8")
            cache = NestedPrefabCache()
            submitted_calls: list[list[tuple[str, Path]]] = []
            execution_calls: list[list[tuple[str, Path]]] = []

            def run_in_reverse(items, worker, *, max_workers=None):
                self.assertIsNone(max_workers)
                submitted = list(items)
                submitted_calls.append(submitted)
                execution_items: list[tuple[str, Path]] = []
                by_item = {}
                for item in reversed(submitted):
                    execution_items.append(item)
                    by_item[item] = worker(item)
                execution_calls.append(execution_items)
                return [by_item[item] for item in submitted]

            with (
                patch(
                    "prefab_sentinel.nested_prefab_cache.run_ordered",
                    side_effect=run_in_reverse,
                ) as run_ordered,
                patch(
                    "prefab_sentinel.nested_prefab_cache.decode_text_file",
                    wraps=decode_text_file,
                ) as decode_text,
                patch(
                    "prefab_sentinel.nested_prefab_cache.split_yaml_blocks",
                    wraps=split_yaml_blocks,
                ) as split_blocks,
            ):
                first = cache.prefetch_children(
                    _host_text(GUID_A, GUID_A),
                    {GUID_A: child},
                    project_root,
                )
                second = cache.prefetch_children(
                    _host_text(GUID_A),
                    {GUID_A: child},
                    project_root,
                )

        self.assertEqual(1, run_ordered.call_count)
        self.assertEqual([(GUID_A, child)], submitted_calls[0])
        self.assertEqual(list(reversed(submitted_calls[0])), execution_calls[0])
        self.assertEqual(1, decode_text.call_count)
        self.assertEqual(3, split_blocks.call_count)
        self.assertEqual([GUID_A, GUID_A], [child.source_guid for child in first.children])
        self.assertEqual([GUID_A], [child.source_guid for child in second.children])
        self.assertIs(first.children[0].text, first.children[1].text)

    def test_concurrent_prefetch_deduplicates_in_flight_source_load(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        with tempfile.TemporaryDirectory() as raw:
            project_root = Path(raw)
            child = project_root / "Assets" / "Child.prefab"
            child.parent.mkdir(parents=True)
            child.write_text(_child_text("Child"), encoding="utf-8")
            cache = NestedPrefabCache()
            first_entered = Event()
            second_entered = Event()
            release_load = Event()
            original_load = cache._load_record
            load_call_count = 0

            def slow_load(source_guid: str, path: Path, root: Path):
                nonlocal load_call_count
                load_call_count += 1
                if load_call_count == 1:
                    first_entered.set()
                else:
                    second_entered.set()
                self.assertTrue(release_load.wait(5))
                return original_load(source_guid, path, root)

            with patch.object(cache, "_load_record", side_effect=slow_load) as load_record:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(
                        cache.prefetch_children,
                        _host_text(GUID_A),
                        {GUID_A: child},
                        project_root,
                    )
                    self.assertTrue(first_entered.wait(5))
                    second = executor.submit(
                        cache.prefetch_children,
                        _host_text(GUID_A),
                        {GUID_A: child},
                        project_root,
                    )
                    self.assertFalse(second_entered.wait(0.2))
                    release_load.set()
                    first_result = first.result(timeout=5)
                    second_result = second.result(timeout=5)

        self.assertEqual(1, load_record.call_count)
        self.assertEqual([GUID_A], [child.source_guid for child in first_result.children])
        self.assertEqual([GUID_A], [child.source_guid for child in second_result.children])
        self.assertIs(first_result.children[0], second_result.children[0])

    def test_concurrent_prefetch_allows_distinct_source_loads_to_overlap(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        with tempfile.TemporaryDirectory() as raw:
            project_root = Path(raw)
            assets = project_root / "Assets"
            assets.mkdir(parents=True)
            child_a = assets / "ChildA.prefab"
            child_b = assets / "ChildB.prefab"
            child_a.write_text(_child_text("ChildA"), encoding="utf-8")
            child_b.write_text(_child_text("ChildB"), encoding="utf-8")
            cache = NestedPrefabCache()
            first_entered = Event()
            second_entered = Event()
            release_loads = Event()
            original_load = cache._load_record

            def slow_load(source_guid: str, path: Path, root: Path):
                if source_guid == GUID_A:
                    first_entered.set()
                elif source_guid == GUID_B:
                    second_entered.set()
                self.assertTrue(release_loads.wait(5))
                return original_load(source_guid, path, root)

            with patch.object(cache, "_load_record", side_effect=slow_load):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(
                        cache.prefetch_children,
                        _host_text(GUID_A),
                        {GUID_A: child_a},
                        project_root,
                    )
                    self.assertTrue(first_entered.wait(5))
                    second = executor.submit(
                        cache.prefetch_children,
                        _host_text(GUID_B),
                        {GUID_B: child_b},
                        project_root,
                    )
                    try:
                        self.assertTrue(second_entered.wait(5))
                    finally:
                        release_loads.set()
                    first_result = first.result(timeout=5)
                    second_result = second.result(timeout=5)

        self.assertEqual([GUID_A], [child.source_guid for child in first_result.children])
        self.assertEqual([GUID_B], [child.source_guid for child in second_result.children])

    def test_records_resolved_unresolved_and_unreadable_children(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project_root = Path(raw)
            assets = project_root / "Assets"
            assets.mkdir()
            resolved = assets / "Resolved.prefab"
            resolved.write_text(_child_text("Resolved"), encoding="utf-8")
            unreadable = assets / "Unreadable.prefab"
            unreadable.write_bytes(b"\xff")

            result = NestedPrefabCache().prefetch_children(
                _host_text(GUID_A, MISSING_GUID, GUID_B),
                {GUID_A: resolved, GUID_B: unreadable},
                project_root,
            )

        resolved_record, missing_record, unreadable_record = result.children
        self.assertEqual((GUID_A, resolved, "Assets/Resolved.prefab"), (
            resolved_record.source_guid,
            resolved_record.path,
            resolved_record.rel_posix,
        ))
        self.assertIsNotNone(resolved_record.text)
        self.assertEqual(1, len(resolved_record.blocks))
        self.assertIsNone(resolved_record.diagnostic)
        self.assertEqual((MISSING_GUID, None, None, "NESTED_PREFAB_SOURCE_UNRESOLVED"), (
            missing_record.source_guid,
            missing_record.path,
            missing_record.text,
            missing_record.diagnostic.detail if missing_record.diagnostic else "",
        ))
        self.assertEqual((GUID_B, unreadable, None, "NESTED_PREFAB_SOURCE_UNREADABLE"), (
            unreadable_record.source_guid,
            unreadable_record.path,
            unreadable_record.text,
            unreadable_record.diagnostic.detail if unreadable_record.diagnostic else "",
        ))
        self.assertEqual(
            f"Nested PrefabInstance source GUID {GUID_B} could not be decoded.",
            unreadable_record.diagnostic.evidence if unreadable_record.diagnostic else "",
        )

    def test_prefetch_result_keeps_serialized_order_under_reverse_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project_root = Path(raw)
            assets = project_root / "Assets"
            assets.mkdir()
            paths = {}
            for guid, name in ((GUID_A, "A"), (GUID_B, "B"), (GUID_C, "C")):
                path = assets / f"{name}.prefab"
                path.write_text(_child_text(name), encoding="utf-8")
                paths[guid] = path

            def run_in_reverse(items, worker, *, max_workers=None):
                by_item = {item: worker(item) for item in reversed(list(items))}
                return [by_item[item] for item in items]

            with patch(
                "prefab_sentinel.nested_prefab_cache.run_ordered",
                side_effect=run_in_reverse,
            ):
                result = NestedPrefabCache().prefetch_children(
                    _host_text(GUID_A, GUID_B, GUID_C),
                    paths,
                    project_root,
                )

        self.assertEqual([GUID_A, GUID_B, GUID_C], [
            child.source_guid for child in result.children
        ])


if __name__ == "__main__":
    unittest.main()
