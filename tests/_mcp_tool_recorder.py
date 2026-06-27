from __future__ import annotations

from collections.abc import Callable
from typing import ParamSpec, Protocol, TypeVar, cast

F = TypeVar("F", bound=Callable[..., object])
P = ParamSpec("P")
R = TypeVar("R")


class ToolRegistrar(Protocol):
    def tool(self, *args: object, **kwargs: object) -> Callable[[F], F]: ...


class ToolRecorderServer(ToolRegistrar):
    def __init__(self) -> None:
        self.registered: dict[str, Callable[..., object]] = {}

    def tool(self, *args: object, **kwargs: object) -> Callable[[F], F]:
        explicit_name = kwargs.get("name")

        def decorator(func: F) -> F:
            name = explicit_name if isinstance(explicit_name, str) else func.__name__
            self.registered[name] = func
            return func

        return decorator

    def get(self, name: str) -> Callable[P, R]:
        if name not in self.registered:
            available = ", ".join(sorted(self.registered))
            raise AssertionError(
                f"Tool {name!r} was not registered; available tools: {available}"
            )
        return cast(Callable[P, R], self.registered[name])


def record_tools(register: Callable[..., object], *args: object) -> ToolRecorderServer:
    server = ToolRecorderServer()
    register(server, *args)
    return server
