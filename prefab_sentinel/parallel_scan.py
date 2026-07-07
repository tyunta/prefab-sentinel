from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar, cast

T = TypeVar("T")
R = TypeVar("R")


def default_worker_count() -> int:
    process_cpu_count = getattr(os, "process_cpu_count", None)
    count = process_cpu_count() if callable(process_cpu_count) else None
    if count is None:
        count = os.cpu_count()
    if count is None or count < 1:
        return 1
    return count


def resolve_worker_count(item_count: int, max_workers: int | None = None) -> int:
    if item_count < 0:
        raise ValueError("item_count must be non-negative")
    if item_count == 0:
        return 0
    if max_workers is not None and max_workers < 1:
        raise ValueError("max_workers must be at least 1")

    resolved_workers = default_worker_count() if max_workers is None else max_workers
    return min(max(resolved_workers, 1), item_count)


def run_ordered(
    items: Sequence[T],
    worker: Callable[[T], R],
    *,
    max_workers: int | None = None,
) -> list[R]:
    worker_count = resolve_worker_count(len(items), max_workers)
    if worker_count == 0:
        return []

    results: list[R | None] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(worker, item): index
            for index, item in enumerate(items)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return cast(list[R], results)
