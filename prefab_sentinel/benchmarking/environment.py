from __future__ import annotations

import platform
from dataclasses import asdict, dataclass

from prefab_sentinel.benchmarking.manifest import BenchmarkConfigurationError


@dataclass(frozen=True, slots=True)
class EnvironmentFingerprint:
    commit: str
    operating_system: str
    cpu: str
    python_version: str
    worker_count: int
    fixture_hash: str

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def capture_environment(commit: str, worker_count: int, fixture_hash: str) -> EnvironmentFingerprint:
    if not commit.strip() or worker_count < 1 or not fixture_hash.strip():
        raise BenchmarkConfigurationError("environment fingerprint inputs are incomplete")
    return EnvironmentFingerprint(
        commit=commit.strip(),
        operating_system=platform.platform(),
        cpu=platform.processor() or platform.machine(),
        python_version=platform.python_version(),
        worker_count=worker_count,
        fixture_hash=fixture_hash,
    )


def assert_same_host_fixture(
    baseline: EnvironmentFingerprint,
    current: EnvironmentFingerprint,
) -> None:
    comparable_fields = (
        "operating_system",
        "cpu",
        "python_version",
        "worker_count",
        "fixture_hash",
    )
    mismatches = tuple(
        name for name in comparable_fields if getattr(baseline, name) != getattr(current, name)
    )
    if baseline.commit == current.commit:
        raise BenchmarkConfigurationError("baseline and current commits must differ")
    if mismatches:
        raise BenchmarkConfigurationError(
            f"baseline and current environments differ: {', '.join(mismatches)}"
        )
