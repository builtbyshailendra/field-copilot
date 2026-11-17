"""Render synthetic faxed referral pages (PNG) from known text, with fax-like noise.

    python scripts/make_faxes.py

Writes packs/tallowmere-health/faxes/<id>.png and <id>.txt (the source text, used as
ground truth by the evaluation and by the fake model). All content is invented.
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "packs" / "tallowmere-health" / "faxes"

PAGES = {
    "fax-1042-p1": """RIVERSIDE SPINE CLINIC          FAX 1-555-0177     11/15/2026 09:02  P.1/1
PRIOR AUTHORIZATION REQUEST - IMAGING
Member ID: TM-55012
Ordering provider: Dr. A. Okafor
Requested: MRI lumbar spine without contrast   CPT: 72148
ICD-10: M54.16
History: Low back pain radiating to the left leg for 8 weeks.
Treatment: Completed 3 weeks of physical therapy, stopped due to travel.
No bowel or bladder changes. No weakness.""",
    "fax-2207-p1": """NORTHGATE ORTHOPEDICS           FAX 1-555-0181     11/17/2026 05:55  P.1/2
URGENT - PRIOR AUTH
Member ID: TM-71880
Ordering provider: Dr. L. Moreau
Requested: MRI knee without contrast   CPT: 73721
ICD-10: S83.241A
History: Twisting injury at football. Knee locking twice since the fall, for 2 weeks.
X-ray: not yet performed.""",
    "fax-3310-p1": """LAKEVIEW SLEEP CENTER           FAX 1-555-0190     11/14/2026 12:40  P.1/1
SLEEP STUDY REFERRAL
Member ID: TM-30944
Ordering provider: Dr. R. Iyer
Requested: Attended polysomnography   CPT: 95810
ICD-10: G47.33
Loud snoring and witnessed apneas for 26 weeks. Epworth 14.
Home sleep test: not done.""",
}


def render(text: str, seed: int) -> Image.Image:
    rng = random.Random(seed)
    width, height = 1240, 900
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("cour.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    y = 60
    for line in text.splitlines():
        draw.text((60 + rng.randint(-2, 2), y), line, fill=rng.randint(0, 40), font=font)
        y += 44
    for _ in range(1800):  # fax speckle
        x, yy = rng.randrange(width), rng.randrange(height)
        img.putpixel((x, yy), rng.randint(0, 120))
    img = img.rotate(rng.uniform(-0.8, 0.8), fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
    return img.point(lambda v: 0 if v < 150 else 255).convert("L")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (page_id, text) in enumerate(PAGES.items()):
        render(text, seed=i).save(OUT / f"{page_id}.png", optimize=True)
        (OUT / f"{page_id}.txt").write_text(text + "\n", encoding="utf-8")
        print("wrote", page_id)


if __name__ == "__main__":
    main()
