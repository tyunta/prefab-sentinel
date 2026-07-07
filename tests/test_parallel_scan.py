from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest


def _parallel_scan_module():
    spec = importlib.util.find_spec("prefab_sentinel.parallel_scan")
    if spec is None:
        raise AssertionError(
            "expected prefab_sentinel.parallel_scan to provide shared scan runner"
        )
    return importlib.import_module("prefab_sentinel.parallel_scan")


class DefaultWorkerCountTests(unittest.TestCase):
    def test_process_cpu_count_is_preferred_over_cpu_count(self) -> None:
        parallel_scan = _parallel_scan_module()

        with (
            patch.object(parallel_scan.os, "process_cpu_count", return_value=12, create=True),
            patch.object(parallel_scan.os, "cpu_count", return_value=64),
        ):
            self.assertEqual(12, parallel_scan.default_worker_count())

    def test_cpu_count_is_used_when_process_count_is_unavailable(self) -> None:
        parallel_scan = _parallel_scan_module()

        with (
            patch.object(parallel_scan.os, "process_cpu_count", new=None, create=True),
            patch.object(parallel_scan.os, "cpu_count", return_value=6),
        ):
            self.assertEqual(6, parallel_scan.default_worker_count())

    def test_unavailable_or_zero_cpu_count_resolves_to_one(self) -> None:
        parallel_scan = _parallel_scan_module()

        cases = [
            (None, None),
            (0, 8),
            (-3, 8),
        ]
        for process_count, cpu_count in cases:
            with self.subTest(process_count=process_count, cpu_count=cpu_count):
                with (
                    patch.object(
                        parallel_scan.os,
                        "process_cpu_count",
                        return_value=process_count,
                        create=True,
                    ),
                    patch.object(parallel_scan.os, "cpu_count", return_value=cpu_count),
                ):
                    self.assertEqual(1, parallel_scan.default_worker_count())


class ResolveWorkerCountTests(unittest.TestCase):
    def test_worker_count_clamps_default_and_override_to_item_count(self) -> None:
        parallel_scan = _parallel_scan_module()

        with patch.object(parallel_scan, "default_worker_count", return_value=16):
            self.assertEqual(3, parallel_scan.resolve_worker_count(3))

        self.assertEqual(2, parallel_scan.resolve_worker_count(2, max_workers=10))
        self.assertEqual(1, parallel_scan.resolve_worker_count(5, max_workers=1))
        self.assertEqual(0, parallel_scan.resolve_worker_count(0))
        self.assertEqual(0, parallel_scan.resolve_worker_count(0, max_workers=0))

    def test_invalid_worker_count_arguments_raise_pinned_messages(self) -> None:
        parallel_scan = _parallel_scan_module()

        with self.assertRaisesRegex(ValueError, "item_count must be non-negative"):
            parallel_scan.resolve_worker_count(-1)

        with self.assertRaisesRegex(ValueError, "max_workers must be at least 1"):
            parallel_scan.resolve_worker_count(1, max_workers=0)


class _FakeFuture:
    def __init__(self, value: object):
        self._value = value

    def result(self) -> object:
        if isinstance(self._value, BaseException):
            raise self._value
        return self._value


class _FakeExecutor:
    constructed_worker_counts: list[int] = []
    submitted_futures: list[_FakeFuture] = []

    def __init__(self, *, max_workers: int):
        self.constructed_worker_counts.append(max_workers)

    def __enter__(self) -> _FakeExecutor:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def submit(self, worker, item: int) -> _FakeFuture:
        future = _FakeFuture(worker(item))
        self.submitted_futures.append(future)
        return future


class RunOrderedTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeExecutor.constructed_worker_counts = []
        _FakeExecutor.submitted_futures = []

    def test_results_follow_input_order_when_futures_complete_out_of_order(self) -> None:
        parallel_scan = _parallel_scan_module()

        with (
            patch.object(parallel_scan, "default_worker_count", return_value=8),
            patch.object(parallel_scan, "ThreadPoolExecutor", _FakeExecutor),
            patch.object(
                parallel_scan,
                "as_completed",
                side_effect=lambda futures: reversed(list(futures)),
            ),
        ):
            results = parallel_scan.run_ordered([1, 2, 3], lambda value: value * 10)

        self.assertEqual([3], _FakeExecutor.constructed_worker_counts)
        self.assertEqual([10, 20, 30], results)

    def test_empty_input_returns_empty_list_without_constructing_executor(self) -> None:
        parallel_scan = _parallel_scan_module()

        with patch.object(parallel_scan, "ThreadPoolExecutor", _FakeExecutor):
            self.assertEqual([], parallel_scan.run_ordered([], lambda value: value))
            self.assertEqual([], parallel_scan.run_ordered([], lambda value: value, max_workers=0))

        self.assertEqual([], _FakeExecutor.constructed_worker_counts)

    def test_worker_exception_propagates_with_original_message(self) -> None:
        parallel_scan = _parallel_scan_module()

        def fail(_item: int) -> int:
            raise ValueError("worker boom")

        with self.assertRaisesRegex(ValueError, "worker boom"):
            parallel_scan.run_ordered([1], fail, max_workers=1)


@pytest.mark.source_text_invariant
class ThreadPoolExecutorCentralizationTests(unittest.TestCase):
    def test_production_executor_construction_is_centralized_in_parallel_scan(
        self,
    ) -> None:
        project_root = Path(__file__).resolve().parents[1]
        matches: list[tuple[str, str]] = []

        class Visitor(ast.NodeVisitor):
            def __init__(self, path: Path) -> None:
                self._path = path
                self._parents: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._parents.append(node.name)
                self.generic_visit(node)
                self._parents.pop()

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                else:
                    name = ""
                if name == "ThreadPoolExecutor":
                    matches.append((
                        self._path.relative_to(project_root).as_posix(),
                        self._parents[-1] if self._parents else "<module>",
                    ))
                self.generic_visit(node)

        for path in (project_root / "prefab_sentinel").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            Visitor(path).visit(tree)

        self.assertEqual(
            [("prefab_sentinel/parallel_scan.py", "run_ordered")],
            matches,
        )


if __name__ == "__main__":
    unittest.main()
