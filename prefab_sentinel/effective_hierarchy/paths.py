from __future__ import annotations


def _join_symbol_path(parent: str, name: str) -> str:
    if parent:
        return f"{parent}/{name}"
    return name
