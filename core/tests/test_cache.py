"""The semantic answer cache: it saves model calls, and it refuses to reuse an answer when
the question, the documents, the prompt or the tenant differ."""
import pytest

from fieldcopilot.answer.grounded import AnswerResult, GroundedAnswerer
from fieldcopilot.api.assembly import default_llm
from fieldcopilot.cache import SemanticCache, key_terms
from fieldcopilot.orchestrator import Copilot, Specialists
from fieldcopilot.packs import load_pack
from fieldcopilot.rag.chunking import chunk_corpus
from fieldcopilot.rag.documents import load_corpus
from fieldcopilot.rag.embeddings import HashingEmbedder
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.rag.stores import MemoryIndex

QUESTION = "Who approves rollbacks at night and at weekends?"
EMBEDDER = HashingEmbedder()


def answerer(tenant="corvane-freight", extra_chunks=0):
    index = MemoryIndex(EMBEDDER, tenant)
    chunks = chunk_corpus(load_corpus(load_pack(tenant).corpus_dir, tenant))
    index.add(chunks if not extra_chunks else chunks + chunks[:0])
    if extra_chunks:  # a changed corpus: one chunk removed
        index.chunks = index.chunks[:-1]
    llm = default_llm()
    return GroundedAnswerer(llm, Retriever(index, "hybrid"), tenant), llm


def answer_calls(llm) -> int:
    return sum(1 for request, _ in llm.provider.calls if request.purpose == "answer")


def test_key_terms_are_the_numbers_and_identifiers():
    assert key_terms("How often are updates posted during a SEV1?") == {"1", "sev1"}
    assert key_terms("Max replicas for orders-consumer?") == {"orders-consumer"}
    assert key_terms("What does exit code 137 mean after SIGKILL?") == {"137", "sigkill"}
    assert key_terms("who approves rollbacks at night") == frozenset()


def test_an_identical_question_reuses_the_verified_answer_without_a_model_call():
    ans, llm = answerer()
    cache = SemanticCache(EMBEDDER)
    first = ans.answer(QUESTION)
    assert first.status == "answered" and cache.store(QUESTION, first, ans)
    looked = cache.lookup("who approves rollbacks at night and at weekends", ans)
    assert looked.outcome == "hit" and looked.result is first and looked.similarity >= 0.98
    assert answer_calls(llm) == 1


def test_a_near_miss_with_a_different_identifier_is_not_served():
    ans, _ = answerer()
    cache = SemanticCache(EMBEDDER, threshold=0.0, check_sources=False)
    q = "What exit code means the container received SIGKILL?"
    assert cache.store(q, ans.answer(q), ans)
    assert cache.lookup("What exit code means the container received SIGTERM?", ans).outcome == "miss_terms_differ"
    assert cache.lookup("what exit code means the container received SIGKILL", ans).outcome == "hit"


def test_evidence_check_misses_when_the_cited_passages_do_not_come_back(monkeypatch):
    ans, _ = answerer()
    cache = SemanticCache(EMBEDDER, threshold=0.0, check_terms=False)
    cache.store(QUESTION, ans.answer(QUESTION), ans)
    monkeypatch.setattr(ans.retriever, "search", lambda query, k=5: [])
    assert cache.lookup(QUESTION, ans).outcome == "miss_sources_differ"


def test_changed_documents_start_a_fresh_cache():
    ans, _ = answerer()
    changed, _ = answerer(extra_chunks=1)
    cache = SemanticCache(EMBEDDER)
    cache.store(QUESTION, ans.answer(QUESTION), ans)
    assert cache.version(ans) != cache.version(changed)
    assert cache.lookup(QUESTION, changed).outcome == "miss_empty"


def test_entries_expire_and_unverified_answers_are_never_stored():
    ans, _ = answerer()
    now = [1000.0]
    cache = SemanticCache(EMBEDDER, ttl_seconds=60, clock=lambda: now[0])
    cache.store(QUESTION, ans.answer(QUESTION), ans)
    now[0] += 61
    assert cache.lookup(QUESTION, ans).outcome == "miss_expired"
    assert not cache.store("q", AnswerResult("q", "unverified"), ans)
    assert not cache.store("q", AnswerResult("q", "not_in_documents"), ans)


def test_tenants_never_share_answers():
    corvane, _ = answerer("corvane-freight")
    tallowmere, _ = answerer("tallowmere-health")
    cache = SemanticCache(EMBEDDER)
    cache.store(QUESTION, corvane.answer(QUESTION), corvane)
    assert cache.lookup(QUESTION, tallowmere).outcome == "miss_empty"


@pytest.mark.parametrize("mode", ["on", "shadow"])
def test_copilot_serves_hits_only_when_on_and_reports_agreement_in_shadow(mode):
    ans, llm = answerer()
    cache = SemanticCache(EMBEDDER, mode=mode)
    copilot = Copilot(llm, Specialists("corvane-freight", ans, agent=None, agent_route="investigate_incident",
                                       cache=cache), approvals=None)
    first = copilot._specialist("policy_question", QUESTION, None, None)
    second = copilot._specialist("policy_question", QUESTION, None, None)
    assert first.flags == ["cache:miss_empty"]
    if mode == "on":
        assert second.flags == ["cache:hit"] and second.cost_usd == 0.0 and answer_calls(llm) == 1
    else:
        assert second.flags == ["cache:shadow_agree"] and answer_calls(llm) == 2
    assert second.text == first.text
