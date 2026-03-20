"""Fluent API for building patch plans programmatically."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

from prefab_sentinel.patch_plan import PLAN_VERSION, normalize_patch_plan


class PatchPlanBuilder:
    """Builds a patch plan dict using a fluent interface.

    The builder tracks a *current resource* that ops are appended to.
    Call :meth:`add_resource` or :meth:`create_prefab_resource` to switch
    the active resource context.  :meth:`build` validates the result through
    :func:`normalize_patch_plan` so the output is always schema-valid.
    """

    def __init__(self) -> None:
        self._resources: list[dict[str, Any]] = []
        self._resource_ids: set[str] = set()
        self._ops: list[dict[str, Any]] = []
        self._postconditions: list[dict[str, Any]] = []
        self._current_resource_id: str | None = None

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------

    def add_resource(
        self,
        *,
        id: str,
        path: str,
        kind: str | None = None,
        mode: str = "open",
    ) -> Self:
        """Register a resource and make it the active context."""
        if id in self._resource_ids:
            raise ValueError(f"Duplicate resource id '{id}'.")
        resource: dict[str, Any] = {"id": id, "path": path, "mode": mode}
        if kind is not None:
            resource["kind"] = kind
        self._resources.append(resource)
        self._resource_ids.add(id)
        self._current_resource_id = id
        return self

    def create_prefab_resource(self, *, id: str, path: str) -> Self:
        """Shorthand: register a prefab resource in ``create`` mode."""
        return self.add_resource(id=id, path=path, kind="prefab", mode="create")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rid(self) -> str:
        if self._current_resource_id is None:
            raise ValueError("No active resource. Call add_resource() first.")
        return self._current_resource_id

    def _append(self, op: dict[str, Any]) -> Self:
        op["resource"] = self._rid()
        self._ops.append(op)
        return self

    # ------------------------------------------------------------------
    # Create-mode ops
    # ------------------------------------------------------------------

    def create_prefab(self, name: str | None = None, *, result: str | None = None) -> Self:
        op: dict[str, Any] = {"op": "create_prefab"}
        if name is not None:
            op["name"] = name
        if result is not None:
            op["result"] = result
        return self._append(op)

    def create_game_object(
        self, name: str, parent: str, *, result: str | None = None
    ) -> Self:
        op: dict[str, Any] = {"op": "create_game_object", "name": name, "parent": parent}
        if result is not None:
            op["result"] = result
        return self._append(op)

    def add_component(
        self, target: str, type: str, *, result: str | None = None
    ) -> Self:
        op: dict[str, Any] = {"op": "add_component", "target": target, "type": type}
        if result is not None:
            op["result"] = result
        return self._append(op)

    def find_component(
        self, target: str, type: str, *, result: str | None = None
    ) -> Self:
        op: dict[str, Any] = {"op": "find_component", "target": target, "type": type}
        if result is not None:
            op["result"] = result
        return self._append(op)

    def remove_component(self, target: str) -> Self:
        return self._append({"op": "remove_component", "target": target})

    def rename_object(self, target: str, name: str) -> Self:
        return self._append({"op": "rename_object", "target": target, "name": name})

    def reparent(self, target: str, parent: str) -> Self:
        return self._append({"op": "reparent", "target": target, "parent": parent})

    # ------------------------------------------------------------------
    # Property ops (open + create common)
    # ------------------------------------------------------------------

    def set(
        self,
        *,
        target: str | None = None,
        component: str | None = None,
        path: str,
        value: Any,
    ) -> Self:
        op: dict[str, Any] = {"op": "set", "path": path, "value": value}
        if target is not None:
            op["target"] = target
        if component is not None:
            op["component"] = component
        return self._append(op)

    def insert_array_element(
        self,
        *,
        target: str | None = None,
        component: str | None = None,
        path: str,
        index: int,
        value: Any,
    ) -> Self:
        op: dict[str, Any] = {
            "op": "insert_array_element",
            "path": path,
            "index": index,
            "value": value,
        }
        if target is not None:
            op["target"] = target
        if component is not None:
            op["component"] = component
        return self._append(op)

    def remove_array_element(
        self,
        *,
        target: str | None = None,
        component: str | None = None,
        path: str,
        index: int,
    ) -> Self:
        op: dict[str, Any] = {
            "op": "remove_array_element",
            "path": path,
            "index": index,
        }
        if target is not None:
            op["target"] = target
        if component is not None:
            op["component"] = component
        return self._append(op)

    # ------------------------------------------------------------------
    # Lifecycle ops
    # ------------------------------------------------------------------

    def save(self) -> Self:
        return self._append({"op": "save"})

    # ------------------------------------------------------------------
    # Postconditions
    # ------------------------------------------------------------------

    def postcondition(self, type: str, **kwargs: Any) -> Self:
        cond: dict[str, Any] = {"type": type, **kwargs}
        self._postconditions.append(cond)
        return self

    # ------------------------------------------------------------------
    # Build / serialize
    # ------------------------------------------------------------------

    def build(self) -> dict[str, Any]:
        """Build and validate the patch plan through :func:`normalize_patch_plan`."""
        raw: dict[str, Any] = {
            "plan_version": PLAN_VERSION,
            "resources": list(self._resources),
            "ops": list(self._ops),
            "postconditions": list(self._postconditions),
        }
        return normalize_patch_plan(raw)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.build(), ensure_ascii=False, indent=indent)

    def write(self, path: str | Path) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.to_json() + "\n", encoding="utf-8")
