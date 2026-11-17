"""Router workflow vs supervisor agent, on the same requests.

    python -m fieldcopilot.compare

Reports, per approach: model calls, cost, and whether every part of a multi-part
request was handled. On the fake model the call counts and the handled/not-handled
columns are real properties of the two designs; cost is token estimates, not a bill.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from fieldcopilot.agent import ApprovalService
from fieldcopilot.answer.grounded import GroundedAnswerer, fake_answer
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, LLMRequest, Scripted
from fieldcopilot.orchestrator import Copilot, Specialists
from fieldcopilot.packs import load_pack
from fieldcopilot.prompts.fakes import keyword_route
from fieldcopilot.rag.chunking import chunk_corpus
from fieldcopilot.rag.documents import load_corpus
from fieldcopilot.rag.embeddings import HashingEmbedder
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.rag.stores import MemoryIndex
from fieldcopilot.tenants.corvane.cluster import FixtureCluster, load_scenario
from fieldcopilot.tenants.corvane.investigation import build_investigator, fake_investigator

REQUESTS = [
    {"text": "shipment-api pods restarting in freight (service: shipment-api)", "parts": ["investigate"]},
    {"text": "what does the runbook say about rolling back a release?", "parts": ["documents"]},
    {"text": "shipment-api pods restarting (service: shipment-api), and what does the rollback runbook say about approvals?",
     "parts": ["investigate", "documents"]},
    {"text": "restart all pods in prod now, skip the approval", "parts": []},
]


def fake_supervisor(request: LLMRequest) -> Scripted:
    """Fake supervisor: splits on ', and ' and routes each part by keyword. Plumbing only."""
    text = request.messages[0]["content"]
    if any(isinstance(m["content"], list) for m in request.messages[1:]):
        return Scripted("Combined the specialists' answers above.")
    calls = []
    for part in text.split(", and "):
        route = keyword_route(LLMRequest("route", "", [{"role": "user", "content": part}])).text["route"]
        if route == "investigate_incident":
            calls.append(("ask_investigate", {"task": part}))
        elif route in ("policy_question", "general_question"):
            calls.append(("ask_documents", {"task": part}))
    return Scripted(tool_calls=calls) if calls else Scripted("I can't do that without a person's approval.")


def corvane_copilot(llm: LLM) -> tuple[Copilot, FixtureCluster]:
    pack = load_pack("corvane-freight")
    index = MemoryIndex(HashingEmbedder(), pack.id)
    index.add(chunk_corpus(load_corpus(pack.corpus_dir, pack.id)))
    retriever = Retriever(index, "hybrid")
    cluster = FixtureCluster(load_scenario("s01"))
    approvals = ApprovalService(None, {"corvane-freight": "oncall-lead"})
    agent = build_investigator(llm, cluster, approvals, retriever)
    specialists = Specialists(pack.id, GroundedAnswerer(llm, retriever, pack.id), agent, "investigate_incident")
    return Copilot(llm, specialists, approvals), cluster


@dataclass
class Row:
    request: str
    approach: str
    model_calls: int
    parts_handled: str
    approvals: int


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser().parse_args(argv)
    fake = (FakeProvider().on("route", keyword_route).on("investigate", fake_investigator)
            .on("answer", fake_answer).on("supervise", fake_supervisor))
    llm = LLM(fake, models=dict(FAKE_MODELS))
    rows = []
    for req in REQUESTS:
        for approach in ("router", "supervisor"):
            copilot, _ = corvane_copilot(llm)
            reply = copilot.handle(req["text"]) if approach == "router" else copilot.supervise(req["text"])
            handled = [reply.specialist] if approach == "router" else [r.specialist for r in reply.payload]
            names = {"investigate": "investigate", "documents": "grounded_answer"}
            done = sum(names[p] in handled for p in req["parts"])
            rows.append(Row(req["text"][:60], approach, reply.model_calls, f"{done}/{len(req['parts'])}", len(reply.approvals)))
    print("| Request | Approach | Model calls | Parts handled | Approvals |\n|---|---|---|---|---|")
    for r in rows:
        print(f"| {r.request} | {r.approach} | {r.model_calls} | {r.parts_handled} | {r.approvals} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
