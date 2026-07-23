from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from statistics import median


@dataclass(frozen=True, slots=True)
class BenchmarkStatistics:
    median_sec: float
    p95_sec: float
    minimum_sec: float
    maximum_sec: float
    median_absolute_deviation_sec: float


def aggregate_samples(samples: tuple[float, ...]) -> BenchmarkStatistics:
    if not samples:
        raise ValueError("benchmark samples must not be empty")
    ordered = tuple(sorted(samples))
    sample_median = float(median(ordered))
    absolute_deviations = tuple(abs(value - sample_median) for value in ordered)
    return BenchmarkStatistics(
        median_sec=sample_median,
        p95_sec=ordered[ceil(0.95 * len(ordered)) - 1],
        minimum_sec=ordered[0],
        maximum_sec=ordered[-1],
        median_absolute_deviation_sec=float(median(absolute_deviations)),
    )
