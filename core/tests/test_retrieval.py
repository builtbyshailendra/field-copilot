import os

import numpy as np
import pytest

from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, LLMUnavailable
from fieldcopilot.packs import load_pack
from fieldcopilot.rag.bm25 import BM25, tokenize
from fieldcopilot.rag.chunking import Chunk, chunk_corpus
from fieldcopilot.rag.documents import load_corpus
from fieldcopilot.rag.embeddings import HashingEmbedder
from fieldcopilot.rag.evaluate import evaluate, load_questions, score_ranking
from fieldcopilot.rag.rerank import LLMReranker, fake_rerank
from fieldcopilot.rag.retriever import Hit, Retriever, rrf
from fieldcopilot.rag.stores import MemoryIndex


def chunk(cid, text, tenant="t"):
    return Chunk(cid, cid.split("#")[0], tenant, "Doc", ("Doc", cid), text, 0, len(text))


def test_tokenize_keeps_codes_and_splits_hyphens():
    assert tokenize("Exit code 137 on shipment-api, see MP-114") == [
        "exit", "code", "137", "shipment-api", "shipment", "api", "see", "mp-114", "mp", "114"]


def test_bm25_prefers_rare_matching_terms():
    bm = BM25(["pods restarting crash loop", "certificate expired x509", "pods pods pods"])
    scores = bm.scores("x509 certificate")
    assert scores.index(max(scores)) == 1 and scores[0] == 0


def test_hashing_embedder_is_deterministic_and_normalised():
    e = HashingEmbedder()
    a, b = e.embed_documents(["disk pressure on node"]), e.embed_documents(["disk pressure on node"])
    assert np.allclose(a, b) and abs(np.linalg.norm(a[0]) - 1) < 1e-5
    assert e.embed_query("disk pressure") @ a[0] > e.embed_query("certificate rotation") @ a[0]


def test_memory_index_is_per_tenant():
    index = MemoryIndex(HashingEmbedder(), "corvane-freight")
    index.add([chunk("a#1", "rollback the release", "corvane-freight"), chunk("b#1", "lumbar MRI policy", "tallowmere-health")])
    assert [c.id for c, _ in index.vector_search("MRI", 5)] == ["a#1"]
    assert index.get("b#1") is None


def test_rrf_rewards_agreement_between_lists():
    a, b, c = chunk("a#1", "x"), chunk("b#1", "y"), chunk("c#1", "z")
    fused = rrf({"keyword": [a, b, c], "vector": [b, a, c]})
    assert {fused[0].chunk.id, fused[1].chunk.id} == {"a#1", "b#1"} and fused[2].chunk.id == "c#1"
    assert fused[0].sources.keys() == {"keyword", "vector"}
    only_one = rrf({"keyword": [a], "vector": [b, c]})
    assert only_one[0].score == pytest.approx(1 / 61)


def test_score_ranking_metrics():
    hit1, recall5, mrr, ndcg = score_ranking(["x", "a", "b"], {"a", "b"})
    assert hit1 == 0 and recall5 == 1 and mrr == 0.5 and 0 < ndcg < 1
    assert score_ranking(["a"], {"a"})[3] == 1.0


def test_hybrid_search_on_the_real_corpus_finds_obvious_answers():
    pack = load_pack("tallowmere-health")
    index = MemoryIndex(HashingEmbedder(), pack.id)
    index.add(chunk_corpus(load_corpus(pack.corpus_dir, pack.id)))
    hits = Retriever(index, "hybrid").search("AHI needed for a CPAP rental", k=3)
    assert hits[0].chunk.id == "mp-150-cpap#initial-90-day-rental"


def test_evaluation_runs_over_every_labelled_question():
    pack = load_pack("corvane-freight")
    index = MemoryIndex(HashingEmbedder(), pack.id)
    chunks = chunk_corpus(load_corpus(pack.corpus_dir, pack.id))
    ids = {c.id for c in chunks}
    questions = load_questions(pack)
    assert all(set(q["relevant"]) <= ids for q in questions), "a label points at a chunk that doesn't exist"
    index.add(chunks)
    score = evaluate(Retriever(index, "hybrid"), questions, pack.id, "hashing", "hybrid")
    assert score.n == 24 and 0 <= score.mrr <= 1


def test_every_tallowmere_label_exists():
    pack = load_pack("tallowmere-health")
    ids = {c.id for c in chunk_corpus(load_corpus(pack.corpus_dir, pack.id))}
    assert all(set(q["relevant"]) <= ids for q in load_questions(pack))


def test_llm_reranker_keeps_order_on_failure_and_uses_ranking():
    hits = [Hit(chunk(f"d#{i}", f"text {i}"), 0.0) for i in range(3)]
    llm = LLM(FakeProvider().on("rerank", fake_rerank), models=dict(FAKE_MODELS))
    assert [h.chunk.id for h in LLMReranker(llm).rerank("q", hits)] == ["d#0", "d#1", "d#2"]
    down = LLM(FakeProvider().on("rerank", LLMUnavailable("x")), models=dict(FAKE_MODELS), sleep=lambda s: None)
    assert LLMReranker(down).rerank("q", hits) == hits


@pytest.mark.skipif(not os.environ.get("FIELD_TEST_PG_DSN"), reason="needs PostgreSQL with pgvector (CI service)")
def test_pg_index_matches_the_memory_index_interface():
    import psycopg

    from fieldcopilot.rag.stores import PgIndex

    with psycopg.connect(os.environ["FIELD_TEST_PG_DSN"], autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS rag_chunks")
        pack = load_pack("tallowmere-health")
        chunks = chunk_corpus(load_corpus(pack.corpus_dir, pack.id))
        index = PgIndex(conn, HashingEmbedder(), pack.id)
        index.add(chunks)
        other = PgIndex(conn, HashingEmbedder(), "corvane-freight")
        assert other.vector_search("CPAP", 3) == []  # tenant isolation
        hits = Retriever(index, "hybrid").search("AHI needed for a CPAP rental", k=3)
        assert hits[0].chunk.id == "mp-150-cpap#initial-90-day-rental"
        got = index.get("mp-150-cpap#initial-90-day-rental")
        assert got is not None and got.text == next(c.text for c in chunks if c.id == got.id)
