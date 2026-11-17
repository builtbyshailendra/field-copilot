"""Reading faxed pages with Claude's vision.

A faxed referral is an image. The model reads it and returns the fields we ask for,
each with the text it read them from. Unlike typed notes, a quote from an image can't
be checked against source text in code, so every fax-extracted value comes back marked
`needs_confirmation`, and the UI asks the nurse to confirm it against the page. The
evaluation measures accuracy on synthetic faxes rendered from known text.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from fieldcopilot.llm import LLM, Budget, LLMRequest, Scripted
from fieldcopilot.packs import load_pack

FAX_FIELDS = ["member_id", "service_code", "diagnosis_code", "ordering_provider", "symptom_duration_weeks", "therapy_weeks"]


class FaxField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: str
    read_from: str


class FaxReading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[FaxField]
    legible: bool


def fax_dir() -> Path:
    return load_pack("tallowmere-health").root / "faxes"


def read_fax(llm: LLM, page_id: str, budget: Budget | None = None) -> dict:
    image = (fax_dir() / f"{page_id}.png").read_bytes()
    request = LLMRequest(
        purpose="read_fax", role="strong", effort="low", max_tokens=2000, tenant="tallowmere-health",
        system=("You read faxed referral pages for a health plan's review team. Extract only the requested "
                "fields. For each, copy the text you read it from. If a field isn't on the page, leave it out. "
                "Set legible to false if the page can't be read reliably."),
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(image).decode()}},
            {"type": "text", "text": f"Page {page_id}. Fields: {', '.join(FAX_FIELDS)}."},
        ]}],
    )
    reading, response = llm.structured(request, FaxReading, budget)
    return {"page_id": page_id, "legible": reading.legible,
            "fields": [{**f.model_dump(), "status": "needs_confirmation"} for f in reading.fields],
            "cost_usd": response.cost_usd}


def fake_fax_reader(request: LLMRequest) -> Scripted:
    """Fake model: can't see images, so it reads the page's known source text (the fixture
    sidecar). Exercises the request shape and the pipeline, never reported as accuracy."""
    text_part = next(b["text"] for b in request.messages[-1]["content"] if b.get("type") == "text")
    page_id = re.search(r"Page (\S+)\.", text_part).group(1)
    source = (fax_dir() / f"{page_id}.txt").read_text(encoding="utf-8")
    fields = []
    patterns = {
        "member_id": r"Member ID:\s*(\S+)", "service_code": r"CPT:\s*(\d{5})", "diagnosis_code": r"ICD-10:\s*([A-Z]\d+\.?\d*)",
        "ordering_provider": r"Ordering provider:\s*(.+)", "symptom_duration_weeks": r"for (\d+) weeks",
        "therapy_weeks": r"(\d+) weeks of physical therapy",
    }
    for name, pattern in patterns.items():
        m = re.search(pattern, source)
        if m:
            line = next(l for l in source.splitlines() if m.group(0) in l)
            fields.append({"name": name, "value": m.group(1).strip(), "read_from": line.strip()})
    return Scripted({"fields": fields, "legible": True})
