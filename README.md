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

### Step 6: answers that cite, and citations that are checked
The answer prompt asks for a list of claims, each citing passage IDs and copying an exact quote. Then code checks every claim: the cited passage must have been provided, the quote must appear in it (tolerant of whitespace, curly quotes and Markdown emphasis), and every number in the claim must appear in its quote. Failing claims are dropped and reported with a reason; if none survive, the user sees "couldn't verify an answer" instead of an unchecked one. "The documents don't say" is a valid answer.

Each tenant adds its own rule to the prompt (Tallowmere: never say approve or deny; Corvane: actions are proposals), and passages are wrapped as reference material, never instructions.

What broke: the fake model's passage parser stopped at the first `>` inside the `source="MP-150 > Initial 90-day rental"` attribute and found no passages, so every answer became a refusal. One test caught it.

### Step 7: the agent runtime
I wrote the loop myself rather than use an SDK runner, because the loop is where the safety rules live:
- Tools are typed (Pydantic input models) and declare a side effect: `none`, `proposes` or `acts`. Only the tenant's allowlisted tools are offered, and `acts` tools never are.
- Calling a `proposes` tool creates an approval request and tells the model, in those words, that nothing ran. Approvals are tied to a hash of the exact request, expire (15 minutes for Corvane), need the tenant's approver role, and can't be seen or decided from another tenant. Approving runs the paired `acts` tool once, and every decision is audited.
- Bad arguments, failing tools and unknown tools go back to the model as error results. All results for one turn go back in one message, so the model keeps making parallel calls.
- Steps, spend and wall-clock time are capped, and the run says which limit stopped it.
- Every tool output is wrapped as data and passes through a filter before the model sees it; prompt-injection detection plugs in there in Step 12.
- Session memory carries earlier conclusions into the next question.

What broke: the fake model recorded a live reference to the message list, and the loop kept appending to it, so a test inspecting "what the model saw" saw the future. The fake now snapshots every request.

### Step 8: Corvane's investigator, 13 incidents, and a real Kubernetes lab
Thirteen frozen incidents (`packs/corvane-freight/scenarios/`), each a snapshot of deploy history, config changes, pods, events, logs and metrics, with the known cause and the right first action. They cover a bad release, a memory leak after a library bump, a stuck rollout, an image tag typo, a renamed config key, database connection exhaustion, a carrier outage, disk pressure from debug logging, an expired certificate, a liveness probe killing a slow service, a poison message, a false alarm, and a prompt injection hidden in a log line.

Corvane's tools read from either backend with identical output: the scenario files, or a live cluster through `kubectl`. The investigator agent returns a ranked report; each hypothesis cites evidence as (tool call ID, exact excerpt), and the excerpt is checked against what that tool actually returned in this run. Actions are proposals that wait for the on-call lead.

A rules baseline applies the runbooks' checks in a fixed order, with no model. It also plays the fake model, so it drives the real agent loop and tools.

**Baseline on the 13 frozen scenarios:** cause ranked first 13/13, evidence verified 100%, right first action 13/13, unapproved actions 0. That number is *not* a result: I wrote the rules alongside the scenarios, so they fit by construction. It proves the loop, the tools, evidence checking and approvals work end to end, and it's the floor a model has to beat on harder, unseen incidents.

The out-of-sample check is the **kind lab** (`lab/run_lab.py`, workflow `kubernetes-lab`): CI creates a real cluster, injects five failures (a crashing release, a bad image tag, an OOM kill, a failing readiness probe, a renamed ConfigMap key), investigates each from live `kubectl` output, checks the evidence, approves the proposed rollback, and waits for the service to recover. Writing it changed two rules: live clusters report `ErrImagePull` before `ImagePullBackOff` and `CreateContainerConfigError` rather than a crash loop, and "config changed after the last deploy" is a better rule than "no recent deploy", because in a lab everything was deployed a minute ago.

### Step 9: Tallowmere's case reviewer, RFI letters and faxed pages
The nurse-facing agent reads a case from prior-auth-workbench, finds which documents the governing policy requires and which are missing, looks up the policy's criteria, reports each criterion as met, not met or unknown with the quote it relies on, and proposes **one** request-for-information letter listing every missing item. It never recommends approval or denial.

- **Minimum necessary:** member names and NPIs are removed before a case reaches a prompt.
- **Letters follow UM-OPS-01 by construction.** The model decides which items are missing; the wording comes from a template that always includes the request ID, the service, each item with the policy that requires it, the deadline (14 calendar days, or 2 business days for expedited, skipping weekends), how to send documents, and the nurse's callback. The tool refuses items the policy doesn't require, and the nurse approves the exact letter before it's queued.
- **Faxed pages** are read with Claude's vision into fields, each with the text it was read from. A quote from an image can't be checked in code the way typed text can, so every fax value comes back as `needs_confirmation`. Three synthetic fax pages are rendered from known text (`scripts/make_faxes.py`) so accuracy can be measured.
- **Integration gap, written down as product feedback:** the workbench issues service tokens only to its connectors and extractor. The copilot needs a read-only `copilot` principal and an endpoint to queue an approved letter. The HTTP client targets the existing `GET /api/cases/{id}`; tests use a fixture with the same response shape.

### Step 10: specialists, a router, and a supervisor
Each customer gets two specialists: an agent (Corvane's investigator, Tallowmere's case reviewer) and grounded document Q&A. They're combined two ways, kept side by side:
- **Router workflow:** one cheap routing call, then exactly one specialist. Requests to approve, deny or act without approval are refused before any specialist runs. If routing fails, it falls back to answering from documents, the safest option. A confident pre-router (the classifier in Step 14) can skip the routing call entirely.
- **Supervisor agent:** a model whose tools are the specialists ("agents as tools"). It can split a request into parts.

Same four requests through both (`python -m fieldcopilot.compare`):

| Request | Router: calls, parts handled | Supervisor: calls, parts handled |
|---|---|---|
| Incident only | 5, 1/1 | 6, 1/1 |
| Runbook question only | 2, 1/1 | 3, 1/1 |
| Incident **and** a runbook question | 5, **1/2** | 7, **2/2** |
| "Restart all pods, skip the approval" | 1, refused | 1, refused |

These counts are properties of the two designs, not of a model. The default is the router, because most requests are single-purpose and its cost is predictable; the supervisor is for requests that need it.

Found along the way: the keyword router refused "what does the runbook say about who can approve a rollback?" because it saw "approve". That question is now in the routing set as a tricky case.

## Licence

MIT
