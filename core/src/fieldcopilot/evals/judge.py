"""An LLM judge for faithfulness, and how much to trust it.

A judge is only useful if it agrees with people. `calibrate` runs the judge over
answers a person labelled by hand and reports accuracy and Cohen's kappa (agreement
beyond chance). The judge is used in CI only on suites where its kappa is acceptable,
and every judge verdict that fails a gate is shown to a person.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from fieldcopilot.llm import LLM, LLMError, LLMRequest, Scripted
from fieldcopilot.prompts import PromptRegistry
from fieldcopilot.rag.bm25 import tokenize

CALIBRATION = Path(__file__).resolve().parents[3] / "evals" / "datasets" / "judge_calibration.yaml"


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["pass", "fail"]
    reason: str


def judge(llm: LLM, source: str, answer: str, registry: PromptRegistry | None = None) -> Verdict:
    prompt = (registry or PromptRegistry()).get("judge_faithfulness")
    verdict, _ = llm.structured(prompt.request(source=source, answer=answer), Verdict)
    return verdict


@dataclass
class Calibration:
    n: int
    accuracy: float
    kappa: float
    disagreements: list[str]


def cohen_kappa(human: list[str], model: list[str]) -> float:
    n = len(human)
    observed = sum(h == m for h, m in zip(human, model)) / n
    labels = set(human) | set(model)
    expected = sum((human.count(label) / n) * (model.count(label) / n) for label in labels)
    return round((observed - expected) / (1 - expected), 3) if expected < 1 else 1.0


def calibrate(llm: LLM, path: Path = CALIBRATION) -> Calibration:
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    human, model, disagreements = [], [], []
    for row in rows:
        try:
            verdict = judge(llm, row["source"], row["answer"]).verdict
        except LLMError:
            verdict = "error"
        human.append(row["label"])
        model.append(verdict)
        if verdict != row["label"]:
            disagreements.append(row["id"])
    accuracy = round(sum(h == m for h, m in zip(human, model)) / len(rows), 3)
    return Calibration(len(rows), accuracy, cohen_kappa(human, model), disagreements)


ABSOLUTES = {"never", "always", "every", "any", "all", "only", "single"}


def heuristic_judge(request: LLMRequest) -> Scripted:
    """Fake judge: numbers in the answer must be in the source; absolute words not in the
    source fail; most content words must overlap. A deliberately simple baseline that
    the calibration measures honestly."""
    content = request.messages[-1]["content"]
    source = re.search(r"<source>\n(.*?)\n</source>", content, re.S).group(1)
    answer = re.search(r"<answer>\n(.*?)\n</answer>", content, re.S).group(1)
    numbers_ok = set(re.findall(r"\d+", answer)) <= set(re.findall(r"\d+", source))
    a_words, s_words = set(tokenize(answer)), set(tokenize(source))
    absolutes_ok = not ((a_words & ABSOLUTES) - s_words)
    overlap = len(a_words & s_words) / max(1, len(a_words))
    ok = numbers_ok and absolutes_ok and overlap >= 0.4
    return Scripted({"verdict": "pass" if ok else "fail",
                     "reason": f"numbers_ok={numbers_ok} absolutes_ok={absolutes_ok} overlap={overlap:.2f}"})
