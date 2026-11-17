"""Typed tools with a declared side effect (ADR 0003).

- side_effect="none": read-only. The agent may call it freely.
- side_effect="proposes": the agent may call it, but it only creates an approval request.
- side_effect="acts": never offered to the model. Only the approval service runs it,
  after a person with the tenant's approver role approves the exact request.

Arguments are validated against a Pydantic model before anything runs. Bad arguments
go back to the model as an error result, never as an exception into the loop.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError

from fieldcopilot.llm import ToolSpec
from fieldcopilot.llm.client import json_schema_for

SideEffect = Literal["none", "proposes", "acts"]


@dataclass
class ToolContext:
    tenant: str
    run_id: str
    user: str = "system"
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    content: Any
    is_error: bool = False
    approval_id: str | None = None

    def as_text(self) -> str:
        return self.content if isinstance(self.content, str) else json.dumps(self.content, default=str, indent=1)


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], ToolResult | Any]
    side_effect: SideEffect = "none"
    # For "proposes" tools: the "acts" tool that runs after approval, and a one-line summary.
    executes: str | None = None
    summarize: Callable[[BaseModel], str] | None = None

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, json_schema_for(self.input_model))

    def validate(self, args: dict[str, Any]) -> BaseModel:
        return self.input_model.model_validate(args)


class ToolError(Exception):
    pass


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolError(f"duplicate tool {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError(f"no tool {name!r}")
        return self._tools[name]

    def offered(self, allowlist: list[str] | None = None) -> list[Tool]:
        """Tools the model may see: never 'acts' tools, and only those the tenant enabled."""
        return [t for t in self._tools.values()
                if t.side_effect != "acts" and (allowlist is None or t.name in allowlist)]

    def names(self) -> list[str]:
        return list(self._tools)


def run_tool(tool: Tool, ctx: ToolContext, args: dict[str, Any]) -> tuple[ToolResult, BaseModel | None]:
    try:
        parsed = tool.validate(args)
    except ValidationError as error:
        return ToolResult(f"Invalid arguments for {tool.name}: {error.errors()[:3]}", is_error=True), None
    try:
        out = tool.handler(ctx, parsed)
    except Exception as error:  # a tool failing is information for the model, not a crash
        return ToolResult(f"{tool.name} failed: {type(error).__name__}: {error}", is_error=True), parsed
    return (out if isinstance(out, ToolResult) else ToolResult(out)), parsed
