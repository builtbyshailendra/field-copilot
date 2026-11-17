"""Request-for-information letters that follow UM-OPS-01 by construction.

The letter is assembled from a template, not written by the model, so the parts the
procedure requires can't be forgotten: request ID, service, every missing item with the
policy that requires it, the response deadline (14 calendar days standard, 2 business
days expedited), how to send documents, and the nurse's name and callback number. It
never states or hints at a decision. The model's job is deciding *which* items are
missing; the wording is fixed.
"""
from __future__ import annotations

from datetime import date, timedelta


def add_business_days(start: date, days: int) -> date:
    current = start
    while days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days -= 1
    return current


def response_deadline(letter_date: date, priority: str) -> date:
    return add_business_days(letter_date, 2) if priority == "expedited" else letter_date + timedelta(days=14)


def draft_rfi(case: dict, missing: list[str], labels: dict[str, str], policy: str, nurse: str,
              letter_date: date, callback: str = "1-555-0199") -> str:
    if not missing:
        raise ValueError("an RFI needs at least one missing item")
    deadline = response_deadline(letter_date, case["priority"])
    items = "\n".join(f"{i}. {labels.get(m, m)} (required by {policy.upper().split('-')[0]}-{policy.split('-')[1]})"
                      for i, m in enumerate(missing, 1))
    return (
        f"Date: {letter_date:%d %B %Y}\n"
        f"To: {case['provider']['name']}\n"
        f"Re: Prior authorization request {case['id']}, {case['service']['code']} ({case['service']['name']})\n\n"
        f"To complete our review of this request we need the following documents:\n\n{items}\n\n"
        f"Please send them by {deadline:%d %B %Y}"
        f"{' (2 business days, as this is an expedited request)' if case['priority'] == 'expedited' else ' (14 calendar days)'}"
        f", through the provider portal, by fax to 1-555-0142, or as an electronic attachment (X12 275).\n\n"
        f"If you have questions, call {nurse} at {callback}.\n\n"
        f"Utilization Management, Tallowmere Health Plan"
    )
