from pathlib import Path

import pytest

from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider, LLMUnavailable
from fieldcopilot.prompts import PromptError, PromptRegistry
from fieldcopilot.prompts import lab
from fieldcopilot.prompts.fakes import keyword_route


def test_platform_route_prompt_loads_with_a_stable_version():
    registry = PromptRegistry()
    route = registry.get("route")
    assert route.role == "fast" and route.effort == "low" and route.variables == ("message",)
    assert route.version == PromptRegistry().get("route").version and len(route.version) == 12


def test_render_fills_placeholders_and_rejects_missing_ones():
    route = PromptRegistry().get("route")
    system, user = route.render(message="pods restarting")
    assert user == "Message: pods restarting" and "{{" not in system
    with pytest.raises(PromptError, match="needs"):
        route.render()


def test_pack_override_wins_for_its_tenant_only(tmp_path: Path):
    (tmp_path / "route.yaml").write_text(
        "id: route\ndescription: d\nsystem: custom {{message}}\nuser: '{{message}}'\n", encoding="utf-8")
    registry = PromptRegistry(pack_dirs={"corvane-freight": tmp_path})
    assert registry.get("route", "corvane-freight").source == "pack:corvane-freight"
    assert registry.get("route", "tallowmere-health").source == "platform"
    assert registry.get("route", "corvane-freight").version != registry.get("route").version
    assert {r["source"] for r in registry.catalogue()} == {"platform", "pack:corvane-freight"}


def test_bad_prompt_file_is_rejected(tmp_path: Path):
    (tmp_path / "x.yaml").write_text("id: x\nsystem: s\n", encoding="utf-8")
    with pytest.raises(PromptError, match="missing"):
        PromptRegistry(platform_dir=tmp_path)


def test_request_carries_prompt_settings():
    request = PromptRegistry().get("route").request(message="hi", tenant="corvane-freight")
    assert request.purpose == "route" and request.role == "fast" and request.effort == "low"
    assert request.tenant == "corvane-freight" and request.max_tokens == 300


def test_lab_runs_every_variant_and_reports_errors_per_row():
    examples = lab.load_examples("route")
    assert len(examples) == 37 and sum("tricky" in e.tags for e in examples) == 5
    fake = FakeProvider().on("route", keyword_route)
    llm = LLM(fake, models=dict(FAKE_MODELS))
    results = [lab.run(llm, p, examples, lab.RouteDecision, lambda d: d.route, v)
               for v, p in lab.variants("route", PromptRegistry())]
    assert [r.variant for r in results] == ["baseline", "v2_examples"]
    assert results[0].version != results[1].version
    assert results[0].provider == "fake" and results[0].errors == 0 and results[0].n == 37
    table = lab.to_markdown(results)
    assert "| baseline |" in table and "fake" in table


def test_lab_counts_failures_instead_of_crashing():
    llm = LLM(FakeProvider().on("route", LLMUnavailable("down")), models=dict(FAKE_MODELS), sleep=lambda s: None)
    examples = lab.load_examples("route")[:3]
    result = lab.run(llm, PromptRegistry().get("route"), examples, lab.RouteDecision, lambda d: d.route)
    assert result.errors == 3 and result.score == 0.0


def test_lab_save_names_version_and_provider(tmp_path: Path):
    llm = LLM(FakeProvider().on("route", keyword_route), models=dict(FAKE_MODELS))
    result = lab.run(llm, PromptRegistry().get("route"), lab.load_examples("route")[:2], lab.RouteDecision, lambda d: d.route)
    path = lab.save(result, tmp_path)
    assert path.name == f"baseline-{result.version}-fake.json"
