"""Prompt engineering by measurement.

Run one or more versions of a prompt over the same labelled examples, change one thing
per version, and compare score, cost and latency in one table. Results are saved with
the prompt version and provider, so a number can always be traced to what produced it.

    python -m fieldcopilot.prompts.lab route --mode fake
    python -m fieldcopilot.prompts.lab route --mode anthropic   # needs ANTHROPIC_API_KEY

On the fake model every version scores the same: the fake doesn't read prompts. That
run checks the harness. Real comparisons need --mode anthropic or recorded responses.
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from fieldcopilot.llm import LLM, FakeProvider, LLMError, build_llm
from fieldcopilot.prompts.fakes import keyword_route
from fieldcopilot.prompts.registry import Prompt, PromptRegistry, load_prompt

CORE = Path(__file__).resolve().parents[3]
DATASETS = CORE / "evals" / "datasets"
RESULTS = CORE / "evals" / "results" / "prompts"
EXPERIMENTS = Path(__file__).parent / "experiments"

Route = Literal["investigate_incident", "policy_question", "draft_letter", "case_lookup", "general_question", "out_of_scope"]


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Route
    reason: str


@dataclass
class Example:
    id: str
    inputs: dict[str, Any]
    expected: Any
    tags: tuple[str, ...] = ()


@dataclass
class Row:
    id: str
    expected: Any
    actual: Any
    score: float
    cost_usd: float
    latency_ms: float
    tags: tuple[str, ...] = ()
    error: str | None = None


@dataclass
class RunResult:
    prompt_id: str
    version: str
    variant: str
    provider: str
    n: int
    score: float
    by_tag: dict[str, float]
    cost_usd: float
    p50_ms: float
    p95_ms: float
    errors: int
    rows: list[Row] = field(default_factory=list)


def load_examples(name: str, input_key: str = "message") -> list[Example]:
    raw = yaml.safe_load((DATASETS / f"{name}.yaml").read_text(encoding="utf-8"))
    return [Example(r["id"], {input_key: r[input_key]}, r["expected"], ("tricky",) if r.get("tricky") else ())
            for r in raw]


def run(llm: LLM, prompt: Prompt, examples: list[Example], schema: type[BaseModel],
        pick: Callable[[BaseModel], Any], variant: str = "baseline") -> RunResult:
    rows: list[Row] = []
    for example in examples:
        request = prompt.request(**example.inputs)
        try:
            parsed, response = llm.structured(request, schema)
            actual = pick(parsed)
            rows.append(Row(example.id, example.expected, actual, float(actual == example.expected),
                            response.cost_usd, response.latency_ms, example.tags))
        except LLMError as error:
            rows.append(Row(example.id, example.expected, None, 0.0, 0.0, 0.0, example.tags, type(error).__name__))
    latencies = sorted(r.latency_ms for r in rows if r.error is None) or [0.0]
    tags = sorted({t for r in rows for t in r.tags})
    by_tag = {t: _mean([r.score for r in rows if t in r.tags]) for t in tags}
    return RunResult(
        prompt_id=prompt.id, version=prompt.version, variant=variant,
        provider=getattr(llm.provider, "name", "?"), n=len(rows), score=_mean([r.score for r in rows]),
        by_tag=by_tag, cost_usd=round(sum(r.cost_usd for r in rows), 6),
        p50_ms=round(statistics.median(latencies), 1),
        p95_ms=round(latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))], 1),
        errors=sum(r.error is not None for r in rows), rows=rows,
    )


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def to_markdown(results: list[RunResult]) -> str:
    lines = ["| Variant | Version | Provider | Accuracy | Tricky | Cost (USD) | p50 ms | p95 ms | Errors |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.variant} | `{r.version}` | {r.provider} | {r.score:.0%} | {r.by_tag.get('tricky', 0):.0%} "
                     f"| {r.cost_usd:.4f} | {r.p50_ms} | {r.p95_ms} | {r.errors} |")
    return "\n".join(lines)


def save(result: RunResult, directory: Path = RESULTS) -> Path:
    path = directory / result.prompt_id / f"{result.variant}-{result.version}-{result.provider}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), indent=2, default=str), encoding="utf-8")
    return path


def variants(prompt_id: str, registry: PromptRegistry) -> list[tuple[str, Prompt]]:
    found = [("baseline", registry.get(prompt_id))]
    for path in sorted(EXPERIMENTS.glob(f"{prompt_id}_*.yaml")):
        found.append((path.stem.removeprefix(f"{prompt_id}_"), load_prompt(path, "experiment")))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prompt_id", choices=["route"])
    parser.add_argument("--mode", default="fake")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    fake = FakeProvider().on("route", keyword_route) if args.mode == "fake" else None
    llm = build_llm(args.mode, fake=fake)
    examples = load_examples("route")
    results = [run(llm, prompt, examples, RouteDecision, lambda p: p.route, variant)
               for variant, prompt in variants(args.prompt_id, PromptRegistry())]
    print(to_markdown(results))
    if args.save:
        for result in results:
            print("saved", save(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
