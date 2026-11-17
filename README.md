# field-copilot

**One AI copilot platform, deployed for two very different customers.**

- **Corvane Freight** (fictional logistics company): an on-call copilot that investigates failures in a Kubernetes lab, cites the metrics, logs and runbooks behind each hypothesis, and proposes fixes that only run after a human approves them.
- **Tallowmere Health Plan** (the fictional plan from [prior-auth-workbench](https://github.com/builtbyshailendra/prior-auth-workbench)): a copilot for review nurses that answers policy questions with verified citations, reads faxed pages, and drafts letters a nurse must approve.

Same platform code; each customer is a **pack** of documents, tools, prompts and evaluation sets. That's the core forward-deployed-engineering problem: make one product work inside two businesses without forking it.

> Personal portfolio project on synthetic data. Not in production for anyone. Built pairing with Claude Code.

## Status

Being built step by step. See the build log below.

| Area | What's in it | Status |
|---|---|---|
| Discovery and design | Discovery packs for both customers, workshop plan, architecture, decision records | ✅ Step 1 |
| LLM layer | Fake, recorded and Anthropic providers; structured output; tool use; streaming; retries; fallback; cost | ✅ Step 2 |
| Retrieval | Chunking, contextual retrieval, embeddings, pgvector, full-text, hybrid RRF, rerank, verified citations | ⏳ |
| Agents | Tools, budgets, approvals, memory, supervisor + specialists, MCP server, guardrails | ⏳ |
| Evaluation | Retrieval, faithfulness, agent success, safety; judge calibration; CI gates | ⏳ |
| Product | FastAPI + SSE, multi-tenant RLS, Next.js app, voice notes | ⏳ |
| Production | OTel traces, Prometheus/Grafana, Helm, Argo CD, eval-gated canary, SBOM/signing, Terraform for AWS | ⏳ |
| Delivery | Security questionnaire, pilot plan, ROI, runbooks, handover | ⏳ |

## Where to start reading

1. `docs/discovery/corvane-freight/README.md` and `docs/discovery/tallowmere-health/README.md`: what each customer needs, and how success is measured
2. `docs/architecture.md`: how a question flows through the system
3. `docs/decisions/`: the choices and their trade-offs

## Build log

### Step 1: discovery and design
Wrote the discovery pack for each customer (problem, stakeholders, current workflow, requirements, success metrics, risks), a workshop plan for Corvane's first session, the architecture, and five decision records: one platform with customer packs; the model behind an interface; humans approve every side effect; PostgreSQL for everything; tool output is data, never instructions.

The most useful thing discovery changed: Corvane asked for "AI that fixes incidents", but their time goes into **finding evidence**, which is read-only. So the copilot investigates freely and proposes actions, and a person approves them.

### Step 2: the LLM layer
Everything that talks to a model goes through one class, `LLM` (`core/src/fieldcopilot/llm/`). It picks a model chain by role (strong: Claude Opus 5.5, then Sonnet 5.5; fast: Haiku 5.5, then Sonnet 5.5), retries timeouts, rate limits and server errors with exponential backoff (or the server's `retry-after`), falls back to the next model when one keeps failing or refuses, checks a budget before every call, prices every call, and reports a record to hooks that metrics and tracing will subscribe to.

Three providers sit behind it:
- **fake:** scripted replies by purpose. A call nothing is scripted for is an error, so a test can't pass by accident.
- **recorded:** replays saved real responses. The key covers the prompt, tools, schema and effort, so editing a prompt invalidates its recordings instead of replaying a stale answer.
- **anthropic:** the real API, with structured outputs, strict tools, a cache breakpoint on the system prompt, effort on every call, and server-side refusal fallback on the models that support it. The SDK's own retries are off, so two retry loops don't stack.

Structured output takes a Pydantic model, validates the reply, and gets one repair attempt with the validation errors before giving up.

33 tests, all on the fake model or a stub client. What they can't show is answer quality; that waits for an API key and the recorded evaluations.

## Licence

MIT
