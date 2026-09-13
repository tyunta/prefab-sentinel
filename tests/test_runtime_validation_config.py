"""Regression tests for runtime-validation project-root normalization."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from prefab_sentinel.services.runtime_validation.config import (
    UNITY_PROJECT_PATH_ENV,
    default_runtime_root,
)


def test_default_runtime_root_uses_service_root_when_env_is_empty(
    tmp_path: Path,
) -> None:
    with patch.dict(os.environ, {UNITY_PROJECT_PATH_ENV: ""}, clear=False):
        assert default_runtime_root(tmp_path) == tmp_path


def test_default_runtime_root_converts_windows_env_path_for_wsl_file_io(
    tmp_path: Path,
) -> None:
    windows_root = r"D:\VRChatProject\ExampleProject"
    wsl_root = "/mnt/d/VRChatProject/ExampleProject"

    with (
        patch.dict(
            os.environ,
            {UNITY_PROJECT_PATH_ENV: windows_root},
            clear=False,
        ),
        patch("prefab_sentinel.wsl_compat.is_wsl", return_value=True),
        patch(
            "prefab_sentinel.wsl_compat._wslpath",
            return_value=wsl_root,
        ) as convert,
    ):
        assert default_runtime_root(tmp_path) == Path(wsl_root)

    convert.assert_called_once_with("-u", windows_root)
