"""Centralized JSON serialization and deserialization helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def dump_json(data: Any, **kwargs: Any) -> str:
    """Serialize *data* to a JSON string.

    Defaults to ``ensure_ascii=False`` and ``indent=2``.
    Callers may override any ``json.dumps`` keyword via **kwargs.
    """
    merged = {"ensure_ascii": False, "indent": 2, **kwargs}
    return json.dumps(data, **merged)


def load_json(text: str, **kwargs: Any) -> Any:
    """Deserialize a JSON string.

    Propagates ``json.JSONDecodeError`` without swallowing it.
    """
    return json.loads(text, **kwargs)


def load_json_file(path: str | Path) -> Any:
    """Read a file and parse its content as JSON.

    Raises:
        OSError: If the file cannot be read.
        json.JSONDecodeError: If the content is not valid JSON.
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))
