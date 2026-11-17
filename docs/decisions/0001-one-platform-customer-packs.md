# 0001. One platform, customer packs, no forks

**Status:** accepted · 17 Nov 2026

## Context
Two customers with nothing in common: an SRE team and a health plan's nurses. The easy path is two codebases. Every forward deployed team that takes it ends up maintaining N slightly different products.

## Decision
The platform code is shared. Everything customer-specific lives in a **pack** under `packs/<tenant>/`:
- `tenant.yaml`: models allowed, budgets, tools enabled, approval roles, data residency
- `corpus/`: the documents to retrieve from
- `prompts/`: prompt overrides (versioned)
- `tools.py`: the customer's tools, registered through the platform's tool interface
- `evals/`: labelled questions and scenarios with known answers

A pack may add tools and documents. It may not change how approvals, auditing, budgets or citation checks work.

## Consequences
- Adding a customer is writing a pack, and the evaluation set proves it works.
- Some customer requests will need a platform change. That goes through product feedback (`docs/delivery/product-feedback.md`), not a fork.
- Packs are code, so they are reviewed and tested like code.
