# Load tests and the answer cache

- `questions.json`: 36 document questions for both tenants, weighted the way on-call and review traffic repeats itself, including paraphrases and near-miss questions (marked `near_miss`) that must never get another question's answer.
- `ask.js`: k6. Constant arrival rate, sign in once per user, thresholds on errors, p95 latency, and near-misses answered from cache. CI runs it against the API with the cache off and on (`.github/workflows/deploy.yml`, job `load`).
- `run_local.py`: the same load with the standard library, for a laptop without k6. Closed loop: N simulated people ask, wait for the whole streamed reply, and ask again.

## Results on my laptop (18 Nov 2026)

**Setup:** one API process, local PostgreSQL 16, the **fake model with each call made to take 1 s** (`FIELD_FAKE_LATENCY_MS=1000`, a stand-in for a real model's latency, not a measurement of one), the hashing embedder, 12 simulated users for 60 s. Each mode had its own server start (cold cache).

| | Cache off | Cache on |
|---|---|---|
| Requests completed | 248 | 312 |
| Throughput | 3.6 req/s | 4.45 req/s |
| p50 latency | 2,133 ms | 1,147 ms |
| p95 latency | 11,204 ms | 11,187 ms |
| Errors | 0 | 0 |
| Answers served from cache | 0 | 187 (60%) |
| Answer-model calls | 185 (0.75 per request) | 48 (0.15 per request) |
| Near-miss questions given another question's answer | n/a | **0** |

What this shows and what it doesn't:
- **The saving is the model call.** Answer-model calls per request fell by 80%. With a real model, each saved call is seconds of latency and real money; the 1 s fake delay only stands in for that.
- **p95 didn't move**, and that's honest: the slow tail is the investigator. "When does the OrdersQueueLagHigh alert fire?" is routed to an investigation (13 model steps), and investigations are never cached, because they depend on the live cluster.
- **With the hashing embedder the cache is close to an exact-match cache.** The only reuse across different questions was a case-and-punctuation rewrite of the same question. Every other hit was the same question asked again. Most of the 60% comes from repetition, which real on-call traffic has.

## Why the threshold is 0.98 (evals/results/cache/summary.md)

`python -m fieldcopilot.evals.cache_eval` checks 30 question pairs (paraphrases and near-misses) with both embedders. With bge-small (fastembed):

| Threshold | Guards | Paraphrases reused | Near-misses served the wrong answer |
|---|---|---|---|
| 0.90 | none | 80% | 5 of 13 |
| 0.90 | identifiers + evidence | 73% | 3 of 13 |
| 0.96 | identifiers + evidence | 40% | 1 of 13 |
| 0.97 | identifiers + evidence | 33% | 0 of 13 |
| **0.98** | **identifiers + evidence** | **27%** | **0 of 13** |

The closest near-miss ("max_connections on the **main**" vs "**reporting** PostgreSQL cluster", no identifier differs) scores 0.967, so 0.97 would leave almost no margin. 0 of 13 is a small sample, not proof. That's why the API **defaults to shadow mode**: it records what it would have served and whether a fresh answer agreed (`fieldcopilot_answer_cache_total{outcome="shadow_agree|shadow_disagree"}`). A customer turns it on (`FIELD_ANSWER_CACHE=on`) only after seeing that agreement rate on its own traffic.

## Found by the load test

- The scripted case reviewer crashed when a question was routed to case lookup without a case number. That was 38 of the first 249 requests. Fixed: it now asks which case.
- Two document questions are misrouted in the fake setup (the scikit-learn pre-router, then the keyword stand-in for the model router). "What is the decision deadline for an expedited request?" goes to case lookup (the word "request"), and "When does the OrdersQueueLagHigh alert fire?" goes to the investigator (the word "alert"). Both are now candidates for the router's training data, after a person labels them.
