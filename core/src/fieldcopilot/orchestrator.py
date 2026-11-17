"""Two ways to put the specialists together, kept side by side on purpose.

1. Router workflow (`Copilot.handle`): one cheap routing call, then exactly one
   specialist. Predictable cost and latency. Can't handle "do two things".
2. Supervisor agent (`Copilot.supervise`): a model with one tool per specialist
   ("agents as tools"). It can split a request and call several specialists, at the
   price of extra model calls and less predictable cost.

`compare.py` runs the same requests through both and reports calls, cost and routing.
Specialists per tenant:
- Corvane: incident investigator (agent), runbook Q&A (grounded answers).
- Tallowmere: case reviewer (agent), policy Q&A (grounded answers).
Requests to approve, deny or act without approval are refused before any specialist runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from fieldcopilot.agent import Agent, ApprovalService, Limits, Tool, ToolRegistry
from fieldcopilot.answer.grounded import GroundedAnswerer
from fieldcopilot.llm import LLM, Budget, LLMError
from fieldcopilot.prompts import PromptRegistry
from fieldcopilot.prompts.lab import RouteDecision

REFUSAL = ("I can't do that. Coverage decisions belong to nurses and medical directors, and production actions "
           "need a person's approval. I can look things up, investigate and draft proposals.")


@dataclass
class Reply:
    route: str
    specialist: str
    text: str
    payload: Any = None
    model_calls: int = 0
    cost_usd: float = 0.0
    approvals: list[str] = field(default_factory=list)


@dataclass
class Specialists:
    tenant: str
    answerer: GroundedAnswerer
    agent: Agent                     # investigator (Corvane) or case reviewer (Tallowmere)
    agent_route: str                 # route that goes to the agent


class Delegate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str


class Copilot:
    def __init__(self, llm: LLM, specialists: Specialists, approvals: ApprovalService,
                 registry: PromptRegistry | None = None, pre_router: Callable[[str], tuple[str, float]] | None = None):
        self.llm, self.s, self.approvals = llm, specialists, approvals
        self.registry = registry or PromptRegistry()
        self.pre_router = pre_router  # e.g. the scikit-learn router: (route, confidence)
        self.calls: list[str] = []
        llm.hooks.append(lambda record: self.calls.append(record.purpose))

    # ---- 1. router workflow -------------------------------------------------------
    def route(self, message: str) -> str:
        if self.pre_router:
            route, confidence = self.pre_router(message)
            if confidence >= 0.8:
                return route
        decision, _ = self.llm.structured(self.registry.get("route").request(message=message, tenant=self.s.tenant), RouteDecision)
        return decision.route

    def handle(self, message: str, session: str | None = None) -> Reply:
        start = len(self.calls)
        budget = Budget(max_cost_usd=1.0)
        try:
            route = self.route(message)
        except LLMError:
            route = "general_question"  # if routing fails, answering from documents is the safe default
        reply = self._dispatch(route, message, session, budget)
        reply.model_calls = len(self.calls) - start
        return reply

    def _dispatch(self, route: str, message: str, session: str | None, budget: Budget) -> Reply:
        if route == "out_of_scope":
            return Reply(route, "refusal", REFUSAL)
        if route in (self.s.agent_route, "case_lookup", "draft_letter") or (
                route == "investigate_incident" and self.s.agent_route == "investigate_incident"):
            run = self.s.agent.run(message, session=session)
            text = run.final.summary if run.final is not None else (run.error or run.status)
            return Reply(route, self.s.agent.purpose, text, run.final, cost_usd=run.cost_usd, approvals=run.approvals)
        result = self.s.answerer.answer(message, budget)
        text = " ".join(c.text for c in result.claims) or {
            "not_in_documents": "The documents don't cover that.",
            "unverified": "I found an answer but couldn't verify it against the documents, so I'm not showing it."}[result.status]
        return Reply(route, "grounded_answer", text, result, cost_usd=result.cost_usd)

    # ---- 2. supervisor agent ----------------------------------------------------------
    def supervise(self, message: str, session: str | None = None) -> Reply:
        start = len(self.calls)
        results: list[Reply] = []

        def delegate(route: str):
            def run(ctx, a: Delegate):
                reply = self._dispatch(route, a.task, session, Budget(max_cost_usd=1.0))
                results.append(reply)
                return {"specialist": reply.specialist, "answer": reply.text, "approvals": reply.approvals}
            return run

        tools = ToolRegistry([
            Tool(f"ask_{self.s.agent.purpose}", f"Hand a task to the {self.s.agent.purpose.replace('_', ' ')} specialist.",
                 Delegate, delegate(self.s.agent_route)),
            Tool("ask_documents", "Answer a question from the customer's documents, with verified citations.",
                 Delegate, delegate("policy_question")),
        ])
        system = self.registry.get("supervise").render(tenant=self.s.tenant, agent=self.s.agent.purpose, request=message)[0]
        supervisor = Agent(self.llm, tools, self.approvals, system, self.s.tenant, limits=Limits(max_steps=6, max_cost_usd=1.0),
                           purpose="supervise", effort="medium")
        run = supervisor.run(message, session=session)
        return Reply("supervised", "supervisor", run.final_text, results,
                     model_calls=len(self.calls) - start, cost_usd=run.cost_usd + sum(r.cost_usd for r in results),
                     approvals=[a for r in results for a in r.approvals])
