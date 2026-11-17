"use client";

import Link from "next/link";
import { useState } from "react";
import { ask, post, type Answer, type CaseReview, type GroundedResult, type Investigation, type Step } from "@/lib/api";

function isGrounded(r: Answer["result"]): r is GroundedResult {
  return !!r && "claims" in r;
}
function isInvestigation(r: Answer["result"]): r is Investigation {
  return !!r && "hypotheses" in r;
}
function isReview(r: Answer["result"]): r is CaseReview {
  return !!r && "criteria" in r;
}

export default function AskPage() {
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState<"auto" | "supervise">("auto");
  const [route, setRoute] = useState("");
  const [steps, setSteps] = useState<Step[]>([]);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [rated, setRated] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setRoute(""); setSteps([]); setAnswer(null); setError(""); setRated("");
    try {
      await ask(message, mode, (event, data) => {
        if (event === "route") setRoute(String(data.route));
        if (event === "step") setSteps((s) => [...s, data as unknown as Step]);
        if (event === "answer") setAnswer(data as unknown as Answer);
        if (event === "error") setError(String(data.message));
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  async function rate(rating: "up" | "down") {
    if (!answer) return;
    await post("/v1/feedback", { run_id: answer.run_id, rating, comment: "" });
    setRated(rating);
  }

  return (
    <div className="grid">
      <section>
        <form className="panel" onSubmit={submit}>
          <label htmlFor="message"><b>Ask the copilot</b></label>
          <textarea id="message" rows={3} value={message} onChange={(e) => setMessage(e.target.value)}
            placeholder="e.g. shipment-api pods restarting in freight (service: shipment-api)" />
          <div className="row" style={{ marginTop: 8 }}>
            <button type="submit" disabled={busy || !message.trim()}>{busy ? "Working..." : "Ask"}</button>
            <label className="row muted">
              <input type="checkbox" style={{ width: "auto" }} checked={mode === "supervise"}
                onChange={(e) => setMode(e.target.checked ? "supervise" : "auto")} />
              Split multi-part requests (supervisor)
            </label>
          </div>
        </form>

        {error && <div className="panel" role="alert">{error}</div>}

        {answer && (
          <div className="panel" data-testid="answer">
            <div className="row">
              <span className="chip">{answer.specialist.replace("_", " ")}</span>
              {answer.violations.map((v) => <span key={v} className="chip bad">blocked: {v}</span>)}
              {answer.flags.some((f) => f.includes("possible_injection")) &&
                <span className="chip warn">injection flagged in tool output</span>}
              {answer.flags.some((f) => f.includes("secret_redacted")) &&
                <span className="chip warn">secrets removed from tool output</span>}
              {answer.flags.includes("cache:hit") && <span className="chip">reused a verified answer</span>}
              <span className="muted">${answer.cost_usd.toFixed(4)} · <Link href={`/runs/${answer.run_id}`}>trace</Link></span>
            </div>
            <p>{answer.text}</p>
            {isGrounded(answer.result) && <Grounded result={answer.result} />}
            {isInvestigation(answer.result) && <InvestigationView report={answer.result} />}
            {isReview(answer.result) && <ReviewView review={answer.result} />}
            {answer.approvals.length > 0 && (
              <p>Waiting for approval: {answer.approvals.map((a) => <Link key={a} href="/approvals">{a} </Link>)}. Nothing has run.</p>
            )}
            <div className="row">
              <span className="muted">Was this right?</span>
              <button className="secondary" disabled={!!rated} onClick={() => rate("up")}>Yes</button>
              <button className="secondary" disabled={!!rated} onClick={() => rate("down")}>No</button>
              {rated && <span className="muted">Thanks, recorded.</span>}
            </div>
          </div>
        )}
      </section>

      <aside className="panel">
        <b>Progress</b>
        {route && <p className="muted">Route: <span className="chip">{route}</span></p>}
        <ol className="steps" data-testid="steps">
          {steps.map((s) => <li key={s.index}><b>{s.kind === "tool" ? s.name : "model"}</b> <span className="muted">{s.summary}</span></li>)}
        </ol>
        {!route && <p className="muted">Steps appear here as the copilot works.</p>}
      </aside>
    </div>
  );
}

function Grounded({ result }: { result: GroundedResult }) {
  if (result.status === "not_in_documents") return <p className="chip">The documents don&apos;t cover this.</p>;
  return (
    <div data-testid="citations">
      {result.claims.map((c, i) => (
        <div key={i}>
          <p><b>{c.text}</b></p>
          {c.citations.map((ci) => (
            <blockquote key={ci.chunk_id}>&ldquo;{ci.quote}&rdquo;<br /><span className="muted">{ci.heading}</span></blockquote>
          ))}
        </div>
      ))}
      {result.dropped.length > 0 && (
        <p className="muted">{result.dropped.length} statement(s) removed because their quote didn&apos;t check out.</p>
      )}
    </div>
  );
}

function InvestigationView({ report }: { report: Investigation }) {
  return (
    <div data-testid="investigation">
      <table>
        <thead><tr><th>#</th><th>Likely cause</th><th>Evidence (checked against tool output)</th></tr></thead>
        <tbody>
          {report.hypotheses.map((h, i) => (
            <tr key={i}>
              <td>{i + 1}</td>
              <td><b>{h.cause.replace("_", " ")}</b><br /><span className="muted">{h.statement}</span></td>
              <td>{h.evidence.map((e) => <blockquote key={e.call_id + e.excerpt}>{e.excerpt}</blockquote>)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>Recommended first action: <span className="chip">{report.recommended_action}</span></p>
    </div>
  );
}

function ReviewView({ review }: { review: CaseReview }) {
  const tone = { met: "ok", not_met: "bad", unknown: "warn" } as const;
  return (
    <div data-testid="review">
      <p className="muted">Policy: {review.policy}. The copilot reports what the record shows; decisions are the reviewer&apos;s.</p>
      <table>
        <tbody>
          {review.criteria.map((c, i) => (
            <tr key={i}><td>{c.criterion}</td><td><span className={`chip ${tone[c.status]}`}>{c.status.replace("_", " ")}</span></td>
              <td><blockquote>{c.evidence}</blockquote></td></tr>
          ))}
        </tbody>
      </table>
      {review.missing_documents.length > 0 && <p>Missing: {review.missing_documents.join(", ")}</p>}
    </div>
  );
}
