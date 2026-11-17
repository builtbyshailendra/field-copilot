"""The agent loop: model → tools → model, inside hard limits.

Owned by us rather than an SDK runner, because the loop is where the safety rules live:
- only tools on the tenant's allowlist are offered, and "acts" tools never are;
- "proposes" tools become approval requests, and the model is told plainly that
  nothing ran;
- every tool output is wrapped as data and passed through a filter (prompt-injection
  detection plugs in here) before the model sees it;
- steps, spend and wall-clock time are capped, and the run says which limit stopped it;
- every step is recorded, so a run can be replayed and shown in the trace viewer.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError

from fieldcopilot.agent.approvals import ApprovalService
from fieldcopilot.agent.tools import Tool, ToolContext, ToolRegistry, ToolResult, run_tool
from fieldcopilot.llm import LLM, Budget, BudgetExceeded, LLMError, LLMRequest
from fieldcopilot.llm.client import json_schema_for

RunStatus = Literal["completed", "awaiting_approval", "step_limit", "budget", "time_limit", "error"]
OutputFilter = Callable[[str], tuple[str, list[str]]]


@dataclass
class Limits:
    max_steps: int = 10
    max_cost_usd: float = 0.50
    max_seconds: float = 120.0


@dataclass
class Step:
    index: int
    kind: Literal["model", "tool"]
    name: str
    detail: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_ms: float = 0.0


@dataclass
class AgentRun:
    run_id: str
    tenant: str
    task: str
    status: RunStatus = "completed"
    final_text: str = ""
    final: Any = None
    steps: list[Step] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)  # tool call id -> tool output text
    flags: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def tool_calls(self) -> list[str]:
        return [s.name for s in self.steps if s.kind == "tool"]


class SessionMemory:
    """Short memory per conversation: what was asked and concluded before."""

    def __init__(self, max_items: int = 5):
        self.max_items = max_items
        self._items: dict[str, list[tuple[str, str]]] = {}

    def remember(self, session: str, task: str, conclusion: str) -> None:
        self._items.setdefault(session, []).append((task, conclusion[:600]))
        self._items[session] = self._items[session][-self.max_items:]

    def recall(self, session: str) -> str:
        items = self._items.get(session, [])
        return "\n".join(f"- Asked: {t}\n  Concluded: {c}" for t, c in items)


def wrap_output(tool: str, call_id: str, text: str) -> str:
    return f'<tool_output tool="{tool}" call_id="{call_id}">\n{text}\n</tool_output>'


class Agent:
    def __init__(self, llm: LLM, registry: ToolRegistry, approvals: ApprovalService, system: str,
                 tenant: str, allowlist: list[str] | None = None, limits: Limits | None = None,
                 final_schema: type[BaseModel] | None = None, output_filter: OutputFilter | None = None,
                 memory: SessionMemory | None = None, purpose: str = "agent", effort: str = "medium",
                 clock: Callable[[], float] = time.monotonic):
        self.llm, self.registry, self.approvals = llm, registry, approvals
        self.system, self.tenant, self.allowlist = system, tenant, allowlist
        self.limits = limits or Limits()
        self.final_schema, self.output_filter, self.memory = final_schema, output_filter, memory
        self.purpose, self.effort, self.clock = purpose, effort, clock

    def run(self, task: str, session: str | None = None, extras: dict[str, Any] | None = None) -> AgentRun:
        run = AgentRun(run_id=f"run-{uuid.uuid4().hex[:10]}", tenant=self.tenant, task=task)
        ctx = ToolContext(self.tenant, run.run_id, extras=extras or {})
        tools = self.registry.offered(self.allowlist)
        specs = [t.spec() for t in tools]
        by_name = {t.name: t for t in tools}
        prior = self.memory.recall(session) if (self.memory and session) else ""
        opening = f"Earlier in this conversation:\n{prior}\n\nNow: {task}" if prior else task
        messages: list[dict[str, Any]] = [{"role": "user", "content": opening}]
        budget = Budget(max_cost_usd=self.limits.max_cost_usd)
        started = self.clock()
        schema = json_schema_for(self.final_schema) if self.final_schema else None

        for index in range(1, self.limits.max_steps + 1):
            if self.clock() - started > self.limits.max_seconds:
                run.status = "time_limit"
                break
            request = LLMRequest(purpose=self.purpose, system=self.system, messages=messages, tools=specs,
                                 output_schema=schema, effort=self.effort, tenant=self.tenant)
            try:
                response = self.llm.complete(request, budget)
            except BudgetExceeded:
                run.status = "budget"
                break
            except LLMError as error:
                run.status, run.error = "error", f"{type(error).__name__}: {error}"
                break
            run.steps.append(Step(index, "model", self.purpose, {"stop": response.stop_reason, "text": response.text[:500],
                                                                 "tool_calls": [c.name for c in response.tool_calls]},
                                  response.cost_usd, response.latency_ms))
            messages.append({"role": "assistant", "content": response.assistant_content or response.text})
            if not response.tool_calls:
                self._finish(run, response.text)
                break
            results = [self._call(run, ctx, by_name, call.id, call.name, call.input) for call in response.tool_calls]
            messages.append({"role": "user", "content": results})
        else:
            run.status = "step_limit"

        run.cost_usd = round(budget.spent_usd, 6)
        if run.status == "completed" and run.approvals:
            run.status = "awaiting_approval"
        if self.memory and session and run.final_text:
            self.memory.remember(session, task, run.final_text)
        return run

    def _finish(self, run: AgentRun, text: str) -> None:
        run.final_text = text
        if self.final_schema:
            try:
                run.final = self.final_schema.model_validate_json(text)
            except ValidationError as error:
                run.status, run.error = "error", f"final answer didn't match {self.final_schema.__name__}: {error.errors()[:2]}"

    def _call(self, run: AgentRun, ctx: ToolContext, by_name: dict[str, Tool], call_id: str, name: str,
              args: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        tool = by_name.get(name)
        if tool is None:
            result = ToolResult(f"Tool {name!r} is not available to you.", is_error=True)
        elif tool.side_effect == "proposes":
            result = self._propose(run, ctx, tool, args)
        else:
            result, _ = run_tool(tool, ctx, args)
        text = result.as_text()
        if self.output_filter and not result.is_error:
            text, flags = self.output_filter(text)
            run.flags += [f"{name}:{f}" for f in flags]
        run.evidence[call_id] = text
        run.steps.append(Step(len(run.steps) + 1, "tool", name, {"args": args, "error": result.is_error,
                                                                 "approval": result.approval_id, "output": text[:800]},
                              latency_ms=round((time.perf_counter() - started) * 1000, 2)))
        return {"type": "tool_result", "tool_use_id": call_id, "content": wrap_output(name, call_id, text),
                **({"is_error": True} if result.is_error else {})}

    def _propose(self, run: AgentRun, ctx: ToolContext, tool: Tool, args: dict[str, Any]) -> ToolResult:
        try:
            parsed = tool.validate(args)
        except ValidationError as error:
            return ToolResult(f"Invalid arguments for {tool.name}: {error.errors()[:3]}", is_error=True)
        summary = tool.summarize(parsed) if tool.summarize else f"{tool.name} {parsed.model_dump()}"
        approval = self.approvals.request(ctx.tenant, run.run_id, tool.executes or tool.name, parsed.model_dump(), summary)
        run.approvals.append(approval.id)
        return ToolResult(
            f"Approval request {approval.id} created: {summary}. It has NOT run. A person with the approver role "
            f"must approve it; until then, do not describe it as done.", approval_id=approval.id)
