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

### Step 3: prompts as versioned files, and a harness to measure them
Prompts live in YAML (`core/src/fieldcopilot/prompts/library/`), with their model role, effort and token limit. A prompt's version is a hash of its content, so every result names the exact prompt behind it. A customer pack can override a prompt for its tenant only. Templates use `{{name}}` and a missing variable is an error.

The prompt lab (`python -m fieldcopilot.prompts.lab route`) runs every version of a prompt over the same labelled set (36 routing questions, 4 marked tricky) and prints one table: accuracy, accuracy on the tricky ones, cost, p50/p95 latency, errors. The first experiment adds one worked example per route.

On the fake model both versions score 94%, because the fake is a keyword rule that doesn't read prompts. That run proves the harness, not the prompt. It did show one real thing: the version with examples costs about 40% more input tokens per call, which the real comparison has to justify.

### Step 4: the customers' documents, chunking and contextual retrieval
Each pack now has its documents. Corvane: 11 runbooks, an on-call handbook and 5 postmortems, written to be consistent with each other (the March shipment-api postmortem is why the crash-loop runbook says "read the previous container's logs first"). Tallowmere: 6 synthetic medical policies and 2 operating procedures (RFI letters, CMS-0057-F deadlines), labelled as fictional and not clinical guidance.

The chunker never crosses a heading, keeps each chunk's heading path, splits long sections at paragraph boundaries, and records exact character offsets, which is what lets a citation be checked against the source later. A test checks `doc.text[start:end] == chunk.text` for every chunk.

Contextual retrieval prepends one or two model-written sentences to each chunk before indexing. The whole document sits in the cached system prompt, so only the first chunk of a document pays for it; results are cached on disk by prompt version and content, so re-indexing an unchanged corpus costs nothing. If the model is down, chunks are indexed without context rather than failing the ingest.

### Step 5: search, and measuring it
Two index backends with one interface: PostgreSQL (pgvector HNSW for vectors, built-in full-text search for keywords, every row and query scoped to a tenant), and an in-memory NumPy + BM25 index for this Windows machine, which has no pgvector. CI runs the store tests against a real `pgvector/pgvector:pg17` container. Keyword and vector results are merged with Reciprocal Rank Fusion, then optionally reranked by a local cross-encoder or by the fast model.

Embeddings run locally (BAAI/bge-small-en-v1.5 on CPU), so no document leaves the machine. Tests use a hashing embedder that needs no download.

48 labelled questions (24 per customer), phrased the way an engineer or a nurse would ask rather than copying the documents. Real results, no API key needed:

| Customer | Setup | hit@1 | recall@5 | MRR |
|---|---|---|---|---|
| Corvane | keyword | 0.62 | 0.82 | 0.73 |
| Corvane | vector | 0.75 | 0.94 | 0.84 |
| Corvane | hybrid | 0.67 | 0.94 | 0.79 |
| Corvane | **hybrid + cross-encoder** | **0.79** | 0.90 | **0.87** |
| Tallowmere | keyword | 0.71 | 0.96 | 0.79 |
| Tallowmere | vector | 0.67 | 1.00 | 0.83 |
| Tallowmere | hybrid | 0.79 | 1.00 | 0.87 |
| Tallowmere | **hybrid + cross-encoder** | 0.75 | 1.00 | **0.88** |

What I take from it:
- Hybrid + reranker has the best MRR for both customers, so it's the default. With 24 questions each, one question moves hit@1 by 0.04, so small gaps are noise.
- For Corvane, plain hybrid ranked first less often than vector alone: keyword search over-rewards runbooks that share a service name. The reranker fixes that.
- A templated context sentence ("This section is from X and covers Y") made Corvane's vector search *worse* (hit@1 0.75 → 0.58). Generic context dilutes the embedding. Claude-written contextual retrieval has to beat that, and gets measured when a key exists.

Full tables: `core/evals/results/retrieval/`.

## Licence

MIT
