"""Building a tenant's copilot for one request: LLM, tools, approvals, retrievers, router."""
from __future__ import annotations

import os
from functools import lru_cache

from fieldcopilot.agent import ApprovalService
from fieldcopilot.answer.grounded import GroundedAnswerer, fake_answer
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, build_llm
from fieldcopilot.orchestrator import Copilot, Specialists
from fieldcopilot.wiring import cluster_for_env, retriever_for, workbench_for_env


def default_llm() -> LLM:
    """FIELD_LLM_MODE=fake gives a scripted model so the whole app runs without a key."""
    mode = os.environ.get("FIELD_LLM_MODE", "fake")
    if mode != "fake":
        return build_llm(mode)
    from fieldcopilot.compare import fake_supervisor
    from fieldcopilot.prompts.fakes import keyword_route
    from fieldcopilot.tenants.corvane.investigation import fake_investigator
    from fieldcopilot.tenants.tallowmere.fax import fake_fax_reader
    from fieldcopilot.tenants.tallowmere.tools import fake_reviewer

    fake = (FakeProvider().on("route", keyword_route).on("answer", fake_answer).on("investigate", fake_investigator)
            .on("review_case", fake_reviewer).on("supervise", fake_supervisor).on("read_fax", fake_fax_reader))
    return LLM(fake, models=dict(FAKE_MODELS))


@lru_cache(maxsize=1)
def pre_router():
    from fieldcopilot.ml.router import SklearnRouter

    return SklearnRouter()


def build_copilot(tenant: str, llm: LLM, approvals: ApprovalService) -> Copilot:
    retriever = retriever_for(tenant)
    if tenant == "corvane-freight":
        from fieldcopilot.tenants.corvane.investigation import build_investigator

        agent = build_investigator(llm, cluster_for_env(), approvals, retriever)
        route = "investigate_incident"
    else:
        from fieldcopilot.tenants.tallowmere.tools import build_reviewer

        agent = build_reviewer(llm, workbench_for_env(), approvals, retriever)
        route = "case_lookup"
    specialists = Specialists(tenant, GroundedAnswerer(llm, retriever, tenant), agent, route)
    return Copilot(llm, specialists, approvals, pre_router=pre_router())
