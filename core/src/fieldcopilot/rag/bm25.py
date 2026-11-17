"""BM25 keyword search for the in-memory index (PostgreSQL full-text search does this job
in the database-backed index). Keeps codes like "mp-114", "cpt" and "137" as tokens,
because on-call engineers and nurses search by exactly those."""
from __future__ import annotations

import math
import re
from collections import Counter

TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STOP = set("""a an and are as at be but by can do does for from has have how i if in into is it its
of on or our should so than that the their then there these they this to was we what when which who
why will with you your""".split())


def tokenize(text: str) -> list[str]:
    tokens = []
    for token in TOKEN.findall(text.lower()):
        if token in STOP:
            continue
        tokens.append(token)
        if "-" in token:  # "shipment-api" also matches "shipment" and "api"
            tokens += [part for part in token.split("-") if part and part not in STOP]
    return tokens


class BM25:
    def __init__(self, documents: list[str], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(d)) for d in documents]
        self.lengths = [sum(c.values()) for c in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        df: Counter = Counter()
        for counts in self.docs:
            df.update(counts.keys())
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        out = []
        for counts, length in zip(self.docs, self.lengths):
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * length / (self.avg or 1))
                score += self.idf.get(term, 0.0) * tf * (self.k1 + 1) / denom
            out.append(score)
        return out
