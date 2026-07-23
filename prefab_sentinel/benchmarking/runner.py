from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Protocol

from prefab_sentinel.benchmarking.model import BenchmarkCase, BenchmarkStatus, CaseMeasurement
from prefab_sentinel.benchmarking.statistics import aggregate_samples
from prefab_sentinel.contracts import ToolResponse


class BenchmarkSession(Protocol):
    pass


SessionFactory = Callable[[Path], BenchmarkSession]
Clock = Callable[[], float]


def evaluate_case_status(
    samples: tuple[float, ...],
    *,
    budget_sec: float,
    required_trials: int,
    complete: bool,
) -> BenchmarkStatus:
    if not complete or len(samples) != required_trials:
        return "failed"
    return "passed" if median(samples) < budget_sec else "failed"


def _response_is_complete(response: ToolResponse) -> bool:
    progress = response.data.get("progress")
    progress_partial = isinstance(progress, dict) and progress.get("partial") is True
    return (
        response.success
        and response.data.get("partial") is not True
        and not progress_partial
        and "TIMEOUT" not in response.code
    )


class BenchmarkRunner:
    def __init__(self, session_factory: SessionFactory, clock: Clock = perf_counter) -> None:
        self._session_factory = session_factory
        self._clock = clock

    def measure_case(self, case: BenchmarkCase, project_root: Path) -> CaseMeasurement:
        samples: list[float] = []
        responses: list[ToolResponse] = []
        warmups: list[ToolResponse] = []
        for _ in range(case.measured_trials):
            session = self._session_factory(project_root)
            if case.state == "warm":
                warmups.append(self._dispatch(session, case))
            started = self._clock()
            responses.append(self._dispatch(session, case))
            samples.append(self._clock() - started)
        sample_tuple = tuple(samples)
        complete = all(_response_is_complete(item) for item in (*warmups, *responses))
        return CaseMeasurement(
            case_id=case.case_id,
            samples_sec=sample_tuple,
            statistics=aggregate_samples(sample_tuple),
            response_codes=tuple(item.code for item in responses),
            complete=complete,
            status=evaluate_case_status(
                sample_tuple,
                budget_sec=case.budget_sec,
                required_trials=case.measured_trials,
                complete=complete,
            ),
        )

    @staticmethod
    def _dispatch(session: BenchmarkSession, case: BenchmarkCase) -> ToolResponse:
        method = getattr(session, case.method)
        response = method(**case.arguments)
        if not isinstance(response, ToolResponse):
            raise TypeError(f"{case.method} returned {type(response).__name__}, expected ToolResponse")
        return response
