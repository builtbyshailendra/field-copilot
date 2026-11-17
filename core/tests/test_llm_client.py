import pytest
from pydantic import BaseModel, ConfigDict

from fieldcopilot.llm import (
    FAKE_MODELS, LLM, Budget, BudgetExceeded, FakeProvider, LLMBadOutput, LLMBadRequest, LLMRateLimited,
    LLMRefused, LLMRequest, LLMTimeout, LLMUnavailable, Scripted,
)


def req(purpose="answer", role="strong", **kw):
    return LLMRequest(purpose=purpose, system="You help on-call engineers.", role=role,
                      messages=[{"role": "user", "content": "Why is checkout slow?"}], **kw)


def make(fake, **kw):
    sleeps = []
    llm = LLM(fake, models={"strong": ["fake-strong", "fake-backup"], "fast": ["fake-fast"]},
              sleep=sleeps.append, **kw)
    return llm, sleeps


def test_plain_answer_is_priced_and_reported():
    fake = FakeProvider().on("answer", Scripted("The new release fails its readiness probe."))
    records = []
    llm, _ = make(fake, hooks=[records.append])
    response = llm.complete(req())
    assert response.text.startswith("The new release")
    assert response.cost_usd > 0 and response.attempts == 1 and not response.fell_back
    assert records[0].outcome == "ok" and records[0].purpose == "answer" and records[0].model == "fake-strong"


def test_retryable_errors_back_off_then_succeed():
    fake = FakeProvider().on("answer", LLMTimeout("slow"), LLMUnavailable("503"), Scripted("ok"))
    llm, sleeps = make(fake)
    response = llm.complete(req())
    assert response.text == "ok" and response.attempts == 3
    assert len(sleeps) == 2 and sleeps[1] > 0


def test_rate_limit_honours_retry_after():
    fake = FakeProvider().on("answer", LLMRateLimited("429", retry_after=2.0), Scripted("ok"))
    llm, sleeps = make(fake)
    llm.complete(req())
    assert sleeps == [2.0]


def test_falls_back_to_next_model_when_one_keeps_failing():
    fake = FakeProvider()
    calls = {"n": 0}

    def by_model(request):
        calls["n"] += 1
        return LLMUnavailable("down") if calls["n"] <= 3 else Scripted("served by backup")

    fake.on("answer", by_model)
    llm, _ = make(fake)
    response = llm.complete(req())
    assert response.fell_back and response.model == "fake-backup" and response.attempts == 4


def test_refusal_skips_retries_and_tries_next_model():
    fake = FakeProvider().on("answer", LLMRefused("no"), Scripted("answered by backup"))
    llm, sleeps = make(fake)
    response = llm.complete(req())
    assert response.text == "answered by backup" and sleeps == [] and response.fell_back


def test_bad_request_is_not_retried():
    fake = FakeProvider().on("answer", LLMBadRequest("wrong field"))
    records = []
    llm, sleeps = make(fake, hooks=[records.append])
    with pytest.raises(LLMBadRequest):
        llm.complete(req())
    assert sleeps == [] and records[0].outcome == "error" and records[0].error == "LLMBadRequest"


def test_everything_failing_raises_the_last_error():
    fake = FakeProvider().on("answer", LLMUnavailable("down"))
    llm, _ = make(fake)
    with pytest.raises(LLMUnavailable):
        llm.complete(req())
    assert len(fake.calls) == 6  # 3 attempts x 2 models


def test_budget_stops_a_run():
    fake = FakeProvider().on("answer", Scripted("x" * 4000))
    llm, _ = make(fake)
    budget = Budget(max_cost_usd=0.01)
    llm.complete(req(), budget)
    assert budget.spent_usd > 0.01
    with pytest.raises(BudgetExceeded):
        llm.complete(req(), budget)


def test_unscripted_purpose_is_an_error_not_a_silent_pass():
    llm, _ = make(FakeProvider())
    with pytest.raises(LLMBadRequest, match="no fake script"):
        llm.complete(req("route"))


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str
    confidence: float


def test_structured_output_is_validated():
    fake = FakeProvider().on("answer", Scripted({"cause": "bad release", "confidence": 0.8}))
    llm, _ = make(fake)
    parsed, response = llm.structured(req(), Hypothesis)
    assert parsed.cause == "bad release" and response.parsed is parsed
    sent = fake.calls[0][0]
    assert sent.output_schema["additionalProperties"] is False


def test_structured_output_gets_one_repair_attempt():
    fake = FakeProvider().on("answer", Scripted({"cause": "bad release"}), Scripted({"cause": "bad release", "confidence": 0.7}))
    llm, _ = make(fake)
    parsed, _ = llm.structured(req(), Hypothesis)
    assert parsed.confidence == 0.7
    repair_messages = fake.calls[1][0].messages
    assert "didn't match the schema" in repair_messages[-1]["content"]


def test_structured_output_gives_up_after_the_repair():
    fake = FakeProvider().on("answer", Scripted({"oops": 1}))
    llm, _ = make(fake)
    with pytest.raises(LLMBadOutput):
        llm.structured(req(), Hypothesis)


def test_schema_must_forbid_extra_fields():
    class Loose(BaseModel):
        cause: str

    llm, _ = make(FakeProvider())
    with pytest.raises(ValueError, match="extra='forbid'"):
        llm.structured(req(), Loose)


def test_stream_yields_text_then_final_and_charges_budget():
    fake = FakeProvider().on("answer", Scripted("Roll back release 42 after approval."))
    llm, _ = make(fake)
    budget = Budget()
    events = list(llm.stream(req(), budget))
    assert "".join(e.text for e in events if e.type == "text") == "Roll back release 42 after approval."
    assert events[-1].type == "final" and budget.spent_usd > 0


def test_stream_falls_back_before_any_text_is_shown():
    fake = FakeProvider().on("answer", LLMUnavailable("down"), Scripted("from the fallback path"))
    llm, _ = make(fake)
    events = list(llm.stream(req()))
    assert events[-1].response.text == "from the fallback path"


def test_fast_role_uses_the_fast_chain():
    fake = FakeProvider().on("route", Scripted("investigate"))
    llm = LLM(fake, models=dict(FAKE_MODELS))
    llm.complete(req("route", role="fast"))
    assert fake.calls[0][1] == "fake-fast"
