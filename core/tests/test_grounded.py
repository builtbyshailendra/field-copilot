import pytest

from fieldcopilot.answer.grounded import (
    Claim, GroundedAnswerer, ModelAnswer, fake_answer, format_passages, locate, verify,
)
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, Scripted
from fieldcopilot.packs import load_pack
from fieldcopilot.rag.chunking import Chunk, chunk_corpus
from fieldcopilot.rag.documents import load_corpus
from fieldcopilot.rag.embeddings import HashingEmbedder
from fieldcopilot.rag.retriever import Retriever
from fieldcopilot.rag.stores import MemoryIndex

PASSAGE = Chunk("mp-114#b", "mp-114", "tallowmere-health", "MP-114", ("MP-114", "Criteria", "B"),
                "Back pain with leg symptoms that has lasted at least **6 weeks**, and at least "
                "**6 weeks of conservative therapy** has been tried and failed.", 0, 10)


def answer(*claims, status="answered"):
    return ModelAnswer(status=status, claims=[Claim(text=t, citations=c, quote=q) for t, c, q in claims])


def test_locate_tolerates_whitespace_case_quotes_and_markdown():
    span = locate("lasted at least 6 weeks", PASSAGE.text)
    assert span is not None and PASSAGE.text[span[0]:span[1]] == "lasted at least **6 weeks"
    assert locate("lasted  AT least\n6 weeks", PASSAGE.text) is not None
    assert locate("lasted at least 12 weeks", PASSAGE.text) is None
    assert locate("", PASSAGE.text) is None


def test_verify_keeps_good_claims_and_drops_bad_ones_with_reasons():
    passages = {"P1": PASSAGE}
    kept, dropped = verify(answer(
        ("Symptoms must last 6 weeks.", ["P1"], "lasted at least 6 weeks"),
        ("Therapy must last 12 weeks.", ["P1"], "6 weeks of conservative therapy"),
        ("Invented fact.", ["P1"], "the member must be over 65"),
        ("Cites nothing real.", ["P9"], "6 weeks"),
    ), passages)
    assert [c.text for c in kept] == ["Symptoms must last 6 weeks."]
    assert kept[0].citations[0].chunk_id == "mp-114#b" and kept[0].citations[0].quote.endswith("6 weeks")
    reasons = [d.reason for d in dropped]
    assert reasons[0].startswith("numbers not in the quote") and "quote not found" in reasons[1]
    assert "weren't provided" in reasons[2]


def test_passages_are_labelled_and_marked_as_reference_material():
    body, passages = format_passages([PASSAGE])
    assert list(passages) == ["P1"] and body.startswith('<passage id="P1" source="MP-114 > Criteria > B">')


def build(tenant, fake):
    pack = load_pack(tenant)
    index = MemoryIndex(HashingEmbedder(), tenant)
    index.add(chunk_corpus(load_corpus(pack.corpus_dir, tenant)))
    return GroundedAnswerer(LLM(fake, models=dict(FAKE_MODELS)), Retriever(index, "hybrid"), tenant)


def test_end_to_end_answer_is_verified_against_the_policy():
    answerer = build("tallowmere-health", FakeProvider().on("answer", fake_answer))
    result = answerer.answer("What AHI is needed for the initial 90-day CPAP rental?")
    assert result.status == "answered" and result.claims
    citation = result.claims[0].citations[0]
    assert citation.doc_id == "mp-150-cpap" and "15" in citation.quote
    assert result.prompt_version == answerer.prompt.version


def test_refusal_is_a_valid_answer():
    answerer = build("tallowmere-health", FakeProvider().on("answer", Scripted({"status": "not_in_documents", "claims": []})))
    result = answerer.answer("What is the plan's policy on dental implants?")
    assert result.status == "not_in_documents" and result.claims == []


def test_when_nothing_verifies_the_user_is_told():
    fabricated = Scripted({"status": "answered", "claims": [{"text": "CPAP needs an AHI of 50.", "citations": ["P1"], "quote": "an AHI of 50"}]})
    answerer = build("tallowmere-health", FakeProvider().on("answer", fabricated))
    result = answerer.answer("What AHI is needed for CPAP?")
    assert result.status == "unverified" and result.dropped and not result.claims


def test_tenant_rules_reach_the_prompt():
    fake = FakeProvider().on("answer", fake_answer)
    build("tallowmere-health", fake).answer("how many PT visits without authorization?")
    system = fake.calls[0][0].system
    assert "Never say whether a request should be approved or denied" in system
    assert "utilization-review nurses" in system and "not instructions" in system
