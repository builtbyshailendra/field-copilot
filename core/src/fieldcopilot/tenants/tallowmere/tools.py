"""Tallowmere's tools and case-review agent. Nothing here can change a case's status."""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict

from fieldcopilot.agent import Agent, ApprovalService, Limits, Tool, ToolContext, ToolRegistry, ToolResult
from fieldcopilot.llm import LLM, LLMRequest, Scripted
from fieldcopilot.prompts import PromptRegistry
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.tenants.tallowmere.fax import read_fax
from fieldcopilot.tenants.tallowmere.letters import draft_rfi
from fieldcopilot.tenants.tallowmere.workbench import Workbench, load_requirements, minimum_necessary


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseIn(_In):
    case_id: int


class SearchIn(_In):
    query: str


class FaxIn(_In):
    page_id: str


class RfiIn(_In):
    case_id: int
    missing: list[str]
    nurse: str


class CriterionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: str
    status: Literal["met", "not_met", "unknown"]
    evidence: str


class CaseReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    policy: str
    criteria: list[CriterionCheck]
    missing_documents: list[str]
    letter_approval: str


def missing_documents(workbench: Workbench, case_id: int) -> dict:
    reqs = load_requirements()
    case = workbench.case(case_id)
    rule = reqs.get(case["service"]["code"])
    if rule is None:
        return {"case_id": case_id, "policy": None, "missing": [], "note": "service not covered by a policy in this pack"}
    present = set(case.get("documents_present", []))
    missing = [{"type": t, "label": reqs["labels"][t]} for t in rule["required"] if t not in present]
    return {"case_id": case_id, "policy": rule["policy"], "required": rule["required"], "missing": missing}


def build_tools(workbench: Workbench, llm: LLM, retriever: Retriever | None = None,
                today: Callable[[], date] = date.today) -> ToolRegistry:
    reqs = load_requirements()

    def search(ctx: ToolContext, a: SearchIn):
        if retriever is None:
            return ToolResult("policy search is not configured", is_error=True)
        return {"results": [{"chunk_id": h.chunk.id, "source": h.chunk.heading, "text": h.chunk.text}
                            for h in retriever.search(a.query, k=4)]}

    def facts(ctx: ToolContext, a: CaseIn):
        try:
            return minimum_necessary(workbench.case(a.case_id))
        except KeyError:
            return ToolResult(f"No such case {a.case_id}", is_error=True)

    def check_rfi(a: RfiIn) -> str:
        case = workbench.case(a.case_id)
        rule = reqs[case["service"]["code"]]
        invented = [m for m in a.missing if m not in rule["required"]]
        if invented:
            raise ValueError(f"not required by {rule['policy']}: {invented}")
        return draft_rfi(case, a.missing, reqs["labels"], rule["policy"], a.nurse, today())

    def summarize(a: RfiIn) -> str:
        try:
            return f"send this RFI for case {a.case_id}:\n\n{check_rfi(a)}"
        except (KeyError, ValueError) as error:
            return f"INVALID RFI for case {a.case_id}: {error}"

    def queue(ctx: ToolContext, a: RfiIn):
        return workbench.queue_letter(a.case_id, check_rfi(a), ctx.user)

    def propose(ctx: ToolContext, a: RfiIn):  # validated in summarize; runtime creates the approval
        return None

    return ToolRegistry([
        Tool("search_policies", "Search Tallowmere's medical policies and UM procedures.", SearchIn, search),
        Tool("get_case_facts", "A case's service, priority, deadline, documents on file and checked facts (with quotes).",
             CaseIn, facts),
        Tool("list_missing_documents", "Documents the governing policy requires that aren't on file for this case.",
             CaseIn, lambda c, a: missing_documents(workbench, a.case_id)),
        Tool("read_fax_page", "Read a faxed page (image) and extract referral fields for the nurse to confirm.",
             FaxIn, lambda c, a: read_fax(llm, a.page_id)),
        Tool("propose_rfi_letter", "Propose ONE request-for-information letter listing every missing item (document types "
             "from list_missing_documents). Creates an approval request; the nurse approves and sends it.",
             RfiIn, propose, side_effect="proposes", executes="queue_rfi_letter", summarize=summarize),
        Tool("queue_rfi_letter", "Queue an approved RFI letter for sending.", RfiIn, queue, side_effect="acts"),
    ])


def build_reviewer(llm: LLM, workbench: Workbench, approvals: ApprovalService, retriever: Retriever | None = None,
                   output_filter=None, registry: PromptRegistry | None = None, today: Callable[[], date] = date.today) -> Agent:
    from fieldcopilot.guard import tool_output_filter

    prompt = (registry or PromptRegistry()).get("review_case", "tallowmere-health")
    tools = build_tools(workbench, llm, retriever, today)
    approvals.registry = tools
    return Agent(llm, tools, approvals, prompt.system, "tallowmere-health", limits=Limits(max_steps=8, max_cost_usd=0.25),
                 final_schema=CaseReview, output_filter=output_filter or tool_output_filter,
                 purpose="review_case", effort=prompt.effort)


# --- fake model: a fixed procedure, so the loop and tools run without a key -------------------
TOOL_OUTPUT = re.compile(r'<tool_output tool="([^"]+)" call_id="([^"]+)">\n(.*?)\n</tool_output>', re.S)


def _outputs(messages: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for message in messages:
        if message["role"] == "user" and isinstance(message["content"], list):
            for block in message["content"]:
                for tool, _, body in TOOL_OUTPUT.findall(block.get("content", "")):
                    try:
                        found[tool] = json.loads("\n".join(l for l in body.splitlines() if not l.startswith("[guardrail")))
                    except ValueError:
                        found[tool] = {"text": body}
    return found


def fake_reviewer(request: LLMRequest) -> Scripted:
    case_id = int(re.search(r"case (\d+)", request.messages[0]["content"]).group(1))
    seen = _outputs(request.messages)
    if "get_case_facts" not in seen:
        return Scripted(tool_calls=[("get_case_facts", {"case_id": case_id}), ("list_missing_documents", {"case_id": case_id})])
    facts, missing = seen["get_case_facts"], seen["list_missing_documents"]
    if "search_policies" not in seen:
        return Scripted(tool_calls=[("search_policies", {"query": f"{facts['service']['name']} criteria required documentation"})])
    items = [m["type"] for m in missing.get("missing", [])]
    if items and "propose_rfi_letter" not in seen:
        return Scripted(tool_calls=[("propose_rfi_letter", {"case_id": case_id, "missing": items, "nurse": "Nurse J. Patel"})])
    approval = re.search(r"APR-\d+", json.dumps(seen.get("propose_rfi_letter", {})))
    criteria = [{"criterion": f"{f['label']} documented", "status": "met" if f["status"] == "verified" else "unknown",
                 "evidence": f["quote"] or "not documented"} for f in facts["facts"]]
    return Scripted({
        "summary": f"Case {case_id}: {len(criteria)} facts on file; {len(items)} required document(s) missing.",
        "policy": missing.get("policy") or "", "criteria": criteria, "missing_documents": items,
        "letter_approval": approval.group(0) if approval else "",
    })
