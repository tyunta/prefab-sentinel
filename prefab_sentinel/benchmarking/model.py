from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from prefab_sentinel.benchmarking.statistics import BenchmarkStatistics

BenchmarkState = Literal["cold", "warm"]
BenchmarkStatus = Literal["passed", "failed"]


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    method: str
    state: BenchmarkState
    arguments: Mapping[str, Any]
    measured_trials: int
    budget_sec: float


@dataclass(frozen=True, slots=True)
class CaseMeasurement:
    case_id: str
    samples_sec: tuple[float, ...]
    statistics: BenchmarkStatistics
    response_codes: tuple[str, ...]
    complete: bool
    status: BenchmarkStatus
