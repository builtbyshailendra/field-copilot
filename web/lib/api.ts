// Calls to the copilot API through the Next.js rewrite (/api -> API). Same origin, so the
// httpOnly session cookie is sent automatically; writes add the CSRF header the API requires.

export type Me = { user: string; tenant: string; tenant_name: string; roles: string[]; approver_role: string };

export type Citation = { passage: string; chunk_id: string; doc_id: string; heading: string; quote: string };
export type Claim = { text: string; citations: Citation[] };
export type Dropped = { text: string; reason: string };
export type GroundedResult = { status: "answered" | "not_in_documents" | "unverified"; claims: Claim[]; dropped: Dropped[] };

export type Evidence = { call_id: string; excerpt: string };
export type Hypothesis = { cause: string; statement: string; confidence: number; evidence: Evidence[] };
export type Investigation = { summary: string; hypotheses: Hypothesis[]; recommended_action: string; approvals_requested: string[] };

export type Criterion = { criterion: string; status: "met" | "not_met" | "unknown"; evidence: string };
export type CaseReview = { summary: string; policy: string; criteria: Criterion[]; missing_documents: string[]; letter_approval: string };

export type Answer = {
  run_id: string; route: string; specialist: string; text: string; approvals: string[];
  violations: string[]; flags: string[]; cost_usd: number;
  result: GroundedResult | Investigation | CaseReview | null;
};

export type Step = { index: number; kind: "model" | "tool"; name: string; summary: string };

export type Approval = {
  id: string; tool: string; summary: string; args: Record<string, unknown>; hash: string; status: string;
  created_at: string; expires_at: string; decided_by: string | null; reason: string | null; outcome: string | null; run_id: string;
};

const WRITE_HEADERS = { "Content-Type": "application/json", "X-Requested-With": "field-copilot" };

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`/api${path}`, { cache: "no-store" });
  if (!r.ok) throw new ApiError(r.status, await detail(r));
  return r.json() as Promise<T>;
}

export async function post<T>(path: string, body: unknown = {}): Promise<T> {
  const r = await fetch(`/api${path}`, { method: "POST", headers: WRITE_HEADERS, body: JSON.stringify(body) });
  if (!r.ok) throw new ApiError(r.status, await detail(r));
  return r.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function detail(r: Response): Promise<string> {
  try {
    const body = await r.json();
    return typeof body.detail === "string" ? body.detail : `Request failed (${r.status})`;
  } catch {
    return `Request failed (${r.status})`;
  }
}

// POST /v1/ask returns Server-Sent Events. EventSource only does GET, so read the stream directly.
export async function ask(
  message: string,
  mode: "auto" | "supervise",
  onEvent: (event: string, data: Record<string, unknown>) => void,
): Promise<void> {
  const r = await fetch("/api/v1/ask", { method: "POST", headers: WRITE_HEADERS, body: JSON.stringify({ message, mode }) });
  if (!r.ok || !r.body) throw new ApiError(r.status, await detail(r));
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const event = /^event: (.*)$/m.exec(block)?.[1] ?? "message";
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}
