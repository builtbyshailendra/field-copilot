from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, LLMUnavailable
from fieldcopilot.packs import all_packs, load_pack
from fieldcopilot.rag.chunking import chunk_corpus, chunk_document
from fieldcopilot.rag.contextual import Contextualizer, fake_context
from fieldcopilot.rag.documents import load_corpus, parse

DOC = parse("""---
id: mp-x
title: "MP-X: Test policy"
---
# MP-X: Test policy

Intro line that is long enough to keep.

## Criteria

### A. First criterion
Alpha beta gamma delta epsilon.

Second paragraph of A, still the same section.

### B. Second criterion
Zeta eta theta iota kappa.
""", "tallowmere-health", "mp-x")


def test_packs_load_with_their_settings():
    packs = {p.id: p for p in all_packs()}
    assert set(packs) == {"corvane-freight", "tallowmere-health"}
    assert packs["corvane-freight"].kind == "sre" and packs["corvane-freight"].approver_role == "oncall-lead"
    assert "propose_rollback" in packs["corvane-freight"].enabled_tools
    assert load_pack("tallowmere-health").budget("per_question_usd", 1.0) == 0.25


def test_corpora_load():
    corvane = load_corpus(load_pack("corvane-freight").corpus_dir, "corvane-freight")
    tallowmere = load_corpus(load_pack("tallowmere-health").corpus_dir, "tallowmere-health")
    assert len(corvane) == 17 and len(tallowmere) == 8
    assert {d.type for d in corvane} == {"runbook", "postmortem", "handbook"}
    assert all(d.text and not d.text.startswith("---") for d in corvane + tallowmere)


def test_chunks_never_cross_headings_and_keep_their_path():
    chunks = chunk_document(DOC)
    paths = [c.heading_path for c in chunks]
    assert ("MP-X: Test policy", "Criteria", "A. First criterion") in paths
    assert ("MP-X: Test policy", "Criteria", "B. Second criterion") in paths
    a = next(c for c in chunks if c.heading_path[-1] == "A. First criterion")
    assert "Second paragraph" in a.text and "Zeta" not in a.text


def test_offsets_point_at_the_exact_source_text():
    for doc in load_corpus(load_pack("tallowmere-health").corpus_dir, "tallowmere-health"):
        for chunk in chunk_document(doc):
            assert doc.text[chunk.start:chunk.end] == chunk.text


def test_long_sections_split_at_paragraphs_with_unique_ids():
    long_doc = parse("# T\n\n## S\n" + "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(5)), "t", "long")
    chunks = chunk_document(long_doc, max_words=130)
    assert len(chunks) >= 3 and len({c.id for c in chunks}) == len(chunks)
    assert all(len(c.text.split()) <= 130 or c.text.count("\n\n") == 0 for c in chunks)


def test_whole_corpus_chunk_ids_are_unique():
    for pack in all_packs():
        chunks = chunk_corpus(load_corpus(pack.corpus_dir, pack.id))
        assert len({c.id for c in chunks}) == len(chunks) > 20


def test_contextualizer_caches_by_prompt_version_and_content(tmp_path):
    fake = FakeProvider().on("contextualize", fake_context)
    llm = LLM(fake, models=dict(FAKE_MODELS))
    cache = tmp_path / "ctx.json"
    chunks = chunk_document(DOC)
    Contextualizer(llm, cache_file=cache).contextualize(DOC, chunks)
    assert all(c.context.startswith('This section is from "MP-X: Test policy"') for c in chunks)
    assert chunks[1].index_text.startswith(chunks[1].context)
    first_calls = len(fake.calls)
    again = Contextualizer(llm, cache_file=cache)
    again.contextualize(DOC, chunk_document(DOC))
    assert again.calls == 0 and len(fake.calls) == first_calls


def test_contextualizer_survives_model_failure():
    llm = LLM(FakeProvider().on("contextualize", LLMUnavailable("down")), models=dict(FAKE_MODELS), sleep=lambda s: None)
    ctx = Contextualizer(llm)
    chunks = ctx.contextualize(DOC, chunk_document(DOC))
    assert ctx.failures == len(chunks) and all(c.context == "" for c in chunks)
