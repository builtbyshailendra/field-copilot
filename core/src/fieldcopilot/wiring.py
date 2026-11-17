"""Build each tenant's tools from its pack and environment, in one place.

Backends are chosen by environment so the same code runs in tests, the lab and a real
deployment:
- Corvane: FIELD_CLUSTER=kube reads a live cluster (namespace FIELD_NAMESPACE);
  otherwise a frozen scenario (FIELD_SCENARIO, default s01) for demos.
- Tallowmere: FIELD_WORKBENCH_URL + FIELD_WORKBENCH_TOKEN reach prior-auth-workbench;
  otherwise the synthetic case fixture.
"""
from __future__ import annotations

import os
from functools import lru_cache

from fieldcopilot.agent import ApprovalService, ToolRegistry
from fieldcopilot.llm import LLM, build_llm
from fieldcopilot.packs import load_pack
from fieldcopilot.rag.chunking import chunk_corpus
from fieldcopilot.rag.documents import load_corpus
from fieldcopilot.rag.embeddings import build_embedder
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.rag.stores import MemoryIndex


@lru_cache(maxsize=None)
def retriever_for(tenant: str) -> Retriever:
    pack = load_pack(tenant)
    index = MemoryIndex(build_embedder(), tenant)
    index.add(chunk_corpus(load_corpus(pack.corpus_dir, tenant)))
    return Retriever(index, "hybrid")


def cluster_for_env():
    from fieldcopilot.tenants.corvane.cluster import FixtureCluster, KubeCluster, load_scenario

    if os.environ.get("FIELD_CLUSTER") == "kube":
        return KubeCluster(os.environ.get("FIELD_NAMESPACE", "freight"))
    return FixtureCluster(load_scenario(os.environ.get("FIELD_SCENARIO", "s01")))


def workbench_for_env():
    from fieldcopilot.tenants.tallowmere.workbench import FixtureWorkbench, WorkbenchClient

    url, token = os.environ.get("FIELD_WORKBENCH_URL"), os.environ.get("FIELD_WORKBENCH_TOKEN")
    return WorkbenchClient(url, token) if url and token else FixtureWorkbench.from_pack()


def build_tenant_tools(tenant: str, llm: LLM | None = None) -> tuple[ToolRegistry, ApprovalService, list[str]]:
    pack = load_pack(tenant)
    roles = {tenant: pack.approver_role}
    ttl = {tenant: int(pack.config.get("approvals", {}).get("expires_minutes", 15))}
    if tenant == "corvane-freight":
        from fieldcopilot.tenants.corvane.tools import build_tools

        tools = build_tools(cluster_for_env(), retriever_for(tenant))
    elif tenant == "tallowmere-health":
        from fieldcopilot.tenants.tallowmere.tools import build_tools

        tools = build_tools(workbench_for_env(), llm or build_llm(), retriever_for(tenant))
    else:
        raise KeyError(tenant)
    return tools, ApprovalService(tools, roles, ttl), pack.enabled_tools
