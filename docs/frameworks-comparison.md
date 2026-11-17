# LangGraph and LlamaIndex vs the hand-written versions

I built the same two pieces both ways, on the same tools, data and embeddings, so this is
a comparison from use rather than from documentation.

## Case review with human approval: LangGraph vs my agent loop

| | My agent (`agent/runtime.py` + `approvals.py`) | LangGraph (`frameworks/langgraph_review.py`) |
|---|---|---|
| Code for this flow | Generic loop 157 + approvals 99 lines, reused by both customers; tenant tools 129 | 80 lines for this one flow, on top of the same tools |
| Who decides the steps | The model, inside limits (steps, cost, time) and an allowlist | The graph: fixed order in code; the model only writes the summary |
| Human approval | Approval service: hash-bound, expiring, role-checked, audited; the run ends and a person decides later | `interrupt()` pauses the graph; a checkpointer holds the state; `Command(resume=...)` continues it |
| Model calls per review | 3–5 (tool turns + final) | 1 (summary) |
| Best for | Open-ended work where the steps aren't known: incident investigation | Known procedures: "fetch, check, draft, ask a person, send" |
| Debugging | My own trace (every step and tool output recorded) | LangGraph state per node; LangSmith if allowed (not used here: no external tracing) |

What I'd choose: **LangGraph for Tallowmere's case review**, because the procedure is fixed
by UM-OPS-01 and a graph makes that visible and cheaper (one model call instead of three to
five). **The agent loop for Corvane's investigations**, because which evidence to fetch
next depends on what the last tool returned. Either way the approval rules are the same,
and the `interrupt()` resume still has to go through role and tenant checks; LangGraph
doesn't do that part for you.

## Retrieval: LlamaIndex vs my retriever

With the same chunks and the same embedding model, LlamaIndex's vector retrieval ranks
exactly like mine (MRR within 0.02 on both customers' labelled questions; a test checks
it). The framework adds no accuracy by itself; the gains come from choices above it.

What mine has that the LlamaIndex default setup doesn't, and why I kept it:
- hybrid keyword + vector with Reciprocal Rank Fusion, and a reranker (the best MRR in Step 5);
- tenant scoping on every query (in PostgreSQL, enforced in SQL);
- chunk offsets into the source document, which the citation verifier needs.

What LlamaIndex would give for free if the project grew: many data loaders and vector
store integrations, and query pipelines. If a customer brought 40 SharePoint sites, I'd use
its loaders and keep my retrieval and verification on top. Wrapping my embedder as a
LlamaIndex `BaseEmbedding` took 20 lines, so mixing them is cheap.
