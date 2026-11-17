"""Tallowmere's case review as a LangGraph state graph, with the nurse's approval as an interrupt.

Same tools as the hand-written agent; the difference is control. Here the steps are a
graph fixed in code (fetch case -> missing documents -> policy -> draft letter -> pause
for the nurse -> send or not -> summary), and `interrupt()` suspends the run until a
person resumes it. The checkpointer keeps the state, so the approval can arrive minutes
later from another process.

The one model call is the criteria summary at the end (structured output, our LLM
layer). Everything a workflow can decide without a model, it decides without one.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from fieldcopilot.llm import LLM, LLMRequest
from fieldcopilot.tenants.tallowmere.letters import draft_rfi
from fieldcopilot.tenants.tallowmere.tools import CaseReview, missing_documents
from fieldcopilot.tenants.tallowmere.workbench import Workbench, load_requirements, minimum_necessary


class ReviewState(TypedDict, total=False):
    case_id: int
    nurse: str
    case: dict
    missing: list[str]
    policy: str
    policy_text: list[str]
    letter: str
    decision: dict
    sent: bool
    review: dict


def build_graph(workbench: Workbench, llm: LLM, retriever=None, today: Callable[[], date] = date.today):
    reqs = load_requirements()

    def fetch_case(state: ReviewState) -> ReviewState:
        return {"case": minimum_necessary(workbench.case(state["case_id"]))}

    def find_missing(state: ReviewState) -> ReviewState:
        result = missing_documents(workbench, state["case_id"])
        return {"missing": [m["type"] for m in result["missing"]], "policy": result["policy"] or ""}

    def search_policy(state: ReviewState) -> ReviewState:
        if retriever is None:
            return {"policy_text": []}
        hits = retriever.search(f"{state['case']['service']['name']} criteria", k=3)
        return {"policy_text": [h.chunk.text for h in hits]}

    def draft_letter(state: ReviewState) -> ReviewState:
        full = workbench.case(state["case_id"])
        return {"letter": draft_rfi(full, state["missing"], reqs["labels"], state["policy"], state["nurse"], today())}

    def nurse_approval(state: ReviewState) -> ReviewState:
        decision = interrupt({"letter": state["letter"], "question": "Send this RFI letter?"})
        return {"decision": decision}

    def send(state: ReviewState) -> ReviewState:
        if state["decision"].get("approved"):
            workbench.queue_letter(state["case_id"], state["letter"], state["decision"]["by"])
            return {"sent": True}
        return {"sent": False}

    def summarize(state: ReviewState) -> ReviewState:
        request = LLMRequest(
            purpose="review_case_summary", role="strong", effort="low", max_tokens=3000, tenant="tallowmere-health",
            system="Summarise the case against the policy text. Criteria met / not_met / unknown, with quotes. "
                   "Never recommend approval or denial.",
            messages=[{"role": "user", "content": f"Case: {state['case']}\nPolicy: {state.get('policy_text')}\n"
                                                  f"Missing: {state['missing']}"}])
        review, _ = llm.structured(request, CaseReview)
        return {"review": review.model_dump()}

    graph = StateGraph(ReviewState)
    for name, fn in [("fetch_case", fetch_case), ("find_missing", find_missing), ("search_policy", search_policy),
                     ("draft_letter", draft_letter), ("nurse_approval", nurse_approval), ("send", send),
                     ("summarize", summarize)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "fetch_case")
    graph.add_edge("fetch_case", "find_missing")
    graph.add_edge("find_missing", "search_policy")
    graph.add_conditional_edges("search_policy", lambda s: "draft_letter" if s["missing"] else "summarize")
    graph.add_edge("draft_letter", "nurse_approval")
    graph.add_edge("nurse_approval", "send")
    graph.add_edge("send", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile(checkpointer=InMemorySaver())


def fake_summary(request: LLMRequest) -> Any:
    from fieldcopilot.llm import Scripted

    return Scripted({"summary": "Summary from the graph.", "policy": "", "criteria": [], "missing_documents": [],
                     "letter_approval": ""})
