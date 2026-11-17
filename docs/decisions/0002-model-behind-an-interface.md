# 0002. The model sits behind an interface with fake, recorded and real versions

**Status:** accepted · 17 Nov 2026

## Context
- Tests must not need an API key, money, or network.
- Some customers require the model through a private endpoint in their own cloud account.
- Models change; prices change; outages happen.

## Decision
One `LLMProvider` interface with three implementations:
- **fake:** scripted responses keyed by what the caller asked for. Used by unit tests and CI.
- **recorded:** replays responses saved from real runs (`recordings/`). Used to re-run evaluations for free and to make demos deterministic.
- **anthropic:** the real Claude API (or a private endpoint with the same API).

Above it sit the router (which model for which job), retries with backoff, a fallback chain, and token and cost accounting. Nothing else in the code imports a model SDK.

## Consequences
- Every number in the README says which provider produced it. Fake-model numbers measure the plumbing, never the model.
- The recorded provider can drift from reality; recordings carry the model name and date and are refreshed when prompts change.
