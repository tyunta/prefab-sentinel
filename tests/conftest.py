"""pytest plugin hooks for the prefab-sentinel test suite.

Hosts the ``--regenerate-snapshots`` command-line option used by the
orchestrator snapshot tests (Tasks C2 / D3).  When the option is supplied,
snapshot tests overwrite their fixture files with the live response
payload and pass without comparison; when the option is absent, snapshot
tests compare the live payload against the fixture file and fail with a
diff on any divergence.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--regenerate-snapshots",
        action="store_true",
        default=False,
        help=(
            "Overwrite snapshot fixture files with the live response payload "
            "instead of asserting equality. Use after intentional changes to "
            "the orchestrator response shape."
        ),
    )


@pytest.fixture
def regenerate_snapshots(request: pytest.FixtureRequest) -> bool:
    """Boolean fixture mirroring the ``--regenerate-snapshots`` flag."""
    return bool(request.config.getoption("--regenerate-snapshots"))
