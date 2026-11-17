from fieldcopilot.compare import corvane_copilot
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider
from fieldcopilot.ml.router import ROUTES, SklearnRouter, evaluate, generate
from fieldcopilot.prompts.fakes import keyword_route
from fieldcopilot.prompts.lab import load_examples


def test_training_data_is_balanced_and_disjoint_from_the_test_set():
    data = generate()
    assert {r for _, r in data} == set(ROUTES) and len(data) > 250
    test_messages = {e.inputs["message"].lower().rstrip("?") for e in load_examples("route")}
    assert not ({t.lower().rstrip("? ").removesuffix(" please") for t, _ in data} & test_messages)


def test_held_out_accuracy_and_confident_subset():
    report = evaluate()
    assert report.accuracy >= 0.85 and report.confident_accuracy >= 0.95 and report.confident_share >= 0.5


def test_router_is_deterministic():
    assert SklearnRouter().predict("shipment-api keeps restarting in prod") == SklearnRouter().predict(
        "shipment-api keeps restarting in prod")


def test_confident_classifier_saves_the_routing_call():
    fake = FakeProvider().on("route", keyword_route)
    llm = LLM(fake, models=dict(FAKE_MODELS))
    copilot, _ = corvane_copilot(llm)
    copilot.pre_router = SklearnRouter()
    copilot.handle("give me the database password")
    assert all(call[0].purpose != "route" for call in fake.calls)
