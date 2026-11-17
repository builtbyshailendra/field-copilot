from datetime import date

import pytest
from langgraph.types import Command

from fieldcopilot.frameworks.langgraph_review import build_graph, fake_summary
from fieldcopilot.frameworks.llamaindex_retrieval import LlamaIndexRetriever
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider
from fieldcopilot.packs import load_pack
from fieldcopilot.rag.chunking import chunk_corpus
from fieldcopilot.rag.documents import load_corpus
from fieldcopilot.rag.embeddings import HashingEmbedder
from fieldcopilot.rag.evaluate import evaluate, load_questions
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.rag.stores import MemoryIndex
from fieldcopilot.tenants.tallowmere.workbench import FixtureWorkbench


def llm():
    return LLM(FakeProvider().on("review_case_summary", fake_summary), models=dict(FAKE_MODELS))


def test_graph_pauses_for_the_nurse_and_sends_only_after_resume():
    workbench = FixtureWorkbench.from_pack()
    graph = build_graph(workbench, llm(), today=lambda: date(2026, 11, 20))
    config = {"configurable": {"thread_id": "case-3310"}}
    first = graph.invoke({"case_id": 3310, "nurse": "Nurse J. Patel"}, config)
    pending = first["__interrupt__"][0].value
    assert "Prior authorization request 3310" in pending["letter"] and workbench.sent_letters == []
    final = graph.invoke(Command(resume={"approved": True, "by": "j.patel"}), config)
    assert final["sent"] is True and len(workbench.sent_letters) == 1 and final["review"]["summary"]


def test_graph_rejection_sends_nothing():
    workbench = FixtureWorkbench.from_pack()
    graph = build_graph(workbench, llm(), today=lambda: date(2026, 11, 20))
    config = {"configurable": {"thread_id": "case-2207"}}
    graph.invoke({"case_id": 2207, "nurse": "N"}, config)
    final = graph.invoke(Command(resume={"approved": False, "by": "j.patel"}), config)
    assert final["sent"] is False and workbench.sent_letters == []


def test_graph_skips_the_letter_when_nothing_is_missing():
    workbench = FixtureWorkbench.from_pack()
    final = build_graph(workbench, llm()).invoke({"case_id": 5005, "nurse": "N"}, {"configurable": {"thread_id": "x"}})
    assert "__interrupt__" not in final and final["missing"] == [] and "letter" not in final


@pytest.mark.parametrize("tenant", ["corvane-freight", "tallowmere-health"])
def test_llamaindex_matches_our_vector_search_with_the_same_embedder(tenant):
    pack = load_pack(tenant)
    chunks = chunk_corpus(load_corpus(pack.corpus_dir, tenant))
    embedder = HashingEmbedder()
    index = MemoryIndex(embedder, tenant)
    index.add(chunks)
    ours = evaluate(Retriever(index, "vector"), load_questions(pack), tenant, embedder.name, "vector")
    theirs = evaluate(LlamaIndexRetriever(chunks, embedder), load_questions(pack), tenant, embedder.name, "llamaindex")
    assert abs(ours.mrr - theirs.mrr) < 0.02  # same embeddings, same cosine ranking
