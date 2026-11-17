"""ROI model for both customers. Every input is an assumption to confirm with the customer
and to measure in the pilot; the arithmetic is here so the numbers in roi.md can't drift
from the inputs. Model prices come from the platform's own price table.

    python docs/delivery/roi.py      # rewrites docs/delivery/roi.md
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "src"))
from fieldcopilot.llm.pricing import cost_usd  # noqa: E402
from fieldcopilot.llm.types import Usage  # noqa: E402


def call_cost(model: str, fresh_in: int, cached_in: int, out: int) -> float:
    return cost_usd(model, Usage(input_tokens=fresh_in, output_tokens=out, cache_read_tokens=cached_in))


# --- Corvane Freight: on-call investigations ------------------------------------------
CORVANE = {
    "incidents_per_month": 40,             # pages that need an investigation
    "minutes_on_steps_2_to_5": 25,         # dashboards, deploys, logs, runbook search (discovery, section 4)
    "share_of_that_time_saved": 0.40,      # to be measured in the shadow-mode pilot
    "engineers_per_incident": 1.5,
    "loaded_cost_per_engineer_hour": 60,   # USD
    "doc_questions_per_month": 600,
    "infrastructure_per_month": 600,       # USD, pilot sizing from deploy/terraform/aws; replace with the AWS calculator figure
    # tokens, per model call
    "investigation_steps": 13,             # mean steps in the evaluation suite
    "step_fresh_input": 2000, "step_cached_input": 4000, "step_output": 400,
    "answer_fresh_input": 3000, "answer_cached_input": 1500, "answer_output": 300,
}

# --- Tallowmere Health Plan: review nurses ----------------------------------------------
TALLOWMERE = {
    "nurses": 25,
    "policy_checks_per_nurse_per_day": 12,
    "working_days_per_month": 21,
    "minutes_per_policy_check_now": 6,
    "minutes_per_policy_check_with_copilot": 2,
    "rfi_letters_per_month": 900,
    "minutes_per_letter_now": 12,
    "minutes_per_letter_with_copilot": 3,  # review and approve a drafted letter
    "loaded_cost_per_nurse_hour": 55,      # USD
    "case_reviews_per_month": 900,
    "fax_pages_per_month": 2000,
    "infrastructure_per_month": 400,       # USD, their existing cluster plus the database
    "answer_fresh_input": 3000, "answer_cached_input": 1500, "answer_output": 300,
    "review_steps": 5, "review_fresh_input": 2500, "review_cached_input": 3000, "review_output": 500,
    "fax_input": 1800, "fax_output": 300,
}


def money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def corvane() -> list[tuple[str, str]]:
    c = CORVANE
    hours_saved = (c["incidents_per_month"] * c["minutes_on_steps_2_to_5"] * c["share_of_that_time_saved"]
                   * c["engineers_per_incident"]) / 60
    value = hours_saved * c["loaded_cost_per_engineer_hour"]
    per_investigation = c["investigation_steps"] * call_cost(
        "claude-opus-5-5", c["step_fresh_input"], c["step_cached_input"], c["step_output"])
    per_answer = call_cost("claude-opus-5-5", c["answer_fresh_input"], c["answer_cached_input"], c["answer_output"])
    model = c["incidents_per_month"] * per_investigation + c["doc_questions_per_month"] * per_answer
    cost = model + c["infrastructure_per_month"]
    return [("Engineer hours saved per month", f"{hours_saved:.0f}"),
            ("Value of those hours", f"${value:,.0f}"),
            ("Model cost per investigation (estimate)", f"${per_investigation:.2f}"),
            ("Model cost per document answer (estimate)", f"${per_answer:.3f}"),
            ("Model spend per month", f"${model:,.0f}"),
            ("Infrastructure per month", f"${c['infrastructure_per_month']:,.0f}"),
            ("Net per month, engineer time only", money(value - cost))]


def tallowmere() -> list[tuple[str, str]]:
    t = TALLOWMERE
    checks = t["nurses"] * t["policy_checks_per_nurse_per_day"] * t["working_days_per_month"]
    check_hours = checks * (t["minutes_per_policy_check_now"] - t["minutes_per_policy_check_with_copilot"]) / 60
    letter_hours = t["rfi_letters_per_month"] * (t["minutes_per_letter_now"] - t["minutes_per_letter_with_copilot"]) / 60
    value = (check_hours + letter_hours) * t["loaded_cost_per_nurse_hour"]
    per_answer = call_cost("claude-opus-5-5", t["answer_fresh_input"], t["answer_cached_input"], t["answer_output"])
    per_review = t["review_steps"] * call_cost("claude-opus-5-5", t["review_fresh_input"], t["review_cached_input"],
                                               t["review_output"])
    per_fax = call_cost("claude-opus-5-5", t["fax_input"], 0, t["fax_output"])
    model = checks * per_answer + t["case_reviews_per_month"] * per_review + t["fax_pages_per_month"] * per_fax
    cost = model + t["infrastructure_per_month"]
    return [("Policy checks per month", f"{checks:,}"),
            ("Nurse hours saved on policy checks", f"{check_hours:,.0f}"),
            ("Nurse hours saved on RFI letters", f"{letter_hours:,.0f}"),
            ("Value of those hours", f"${value:,.0f}"),
            ("Model cost per policy answer (estimate)", f"${per_answer:.3f}"),
            ("Model cost per case review (estimate)", f"${per_review:.2f}"),
            ("Model spend per month", f"${model:,.0f}"),
            ("Infrastructure per month", f"${t['infrastructure_per_month']:,.0f}"),
            ("Net per month, nurse time only", money(value - cost))]


def table(rows: list[tuple[str, str]]) -> str:
    return "\n".join(["| | |", "|---|---|", *[f"| {k} | {v} |" for k, v in rows]])


def assumptions(d: dict) -> str:
    return "\n".join(f"- `{k}`: {v}" for k, v in d.items())


def main() -> None:
    doc = f"""# ROI, per customer

Generated by `docs/delivery/roi.py` from the assumptions listed under each table. **Every input is an assumption** to confirm in discovery and measure in the pilot; nothing here is a measured result. Model costs use the platform's price table (`core/src/fieldcopilot/llm/pricing.py`, Claude Opus 5.5 list prices) and token counts per call that are estimates, not measurements.

## Corvane Freight

{table(corvane())}

**What this says:** on engineer time alone, the copilot about breaks even. That's useful to know before the business case is made on the wrong number. Corvane's case rests on **customer-visible minutes**: faster time to first hypothesis on SEV1 and SEV2 incidents (target under 2 minutes, discovery section 6) multiplied by what a minute of shipment-API downtime costs them. That figure has to come from their finance team; it's the first question for the sponsor.

Assumptions:
{assumptions(CORVANE)}

## Tallowmere Health Plan

{table(tallowmere())}

**What this says:** nurse time is the value, mostly from policy look-ups, and model spend is small next to it. Two things aren't counted here and matter to the sponsor: meeting CMS-0057-F's 72-hour and 7-day deadlines, and fewer pended cases from incomplete RFI letters. The pilot's before-and-after measure is minutes per policy check, timed on real cases with the nurses' consent.

Assumptions:
{assumptions(TALLOWMERE)}

## How the pilot replaces these assumptions

| Assumption | How it's measured |
|---|---|
| Minutes on investigation steps 2 to 5 (Corvane) | Shadow-mode pilot: on-call engineers log time to first hypothesis with and without the copilot's investigation open |
| Share of time saved | Same, over at least 20 incidents |
| Minutes per policy check (Tallowmere) | Timed sample of checks before go-live and in week 4 |
| Minutes per letter | Time from "letter needed" to "letter approved" in the workbench audit trail |
| Tokens per call | Real numbers from the platform's own metrics (`fieldcopilot_llm_tokens_total`, cost per run) |
"""
    out = Path(__file__).with_name("roi.md")
    out.write_text(doc, encoding="utf-8", newline="\n")
    print(doc)


if __name__ == "__main__":
    main()
