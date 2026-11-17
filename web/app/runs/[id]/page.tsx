"use client";

import { use, useEffect, useState } from "react";
import { get } from "@/lib/api";

type TraceStep = { index: number; kind: string; name: string; detail: Record<string, unknown>; cost_usd: number; latency_ms: number };
type Run = { id: string; question: string; route: string; kind: string; status: string; answer: string; cost_usd: number; trace: TraceStep[] };

export default function RunTrace({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { get<Run>(`/v1/runs/${id}`).then(setRun).catch((e) => setError(e.message)); }, [id]);
  if (error) return <p role="alert">{error}</p>;
  if (!run) return <p className="muted">Loading...</p>;
  return (
    <>
      <div className="panel">
        <h1>Run {run.id}</h1>
        <p><b>Question:</b> {run.question}</p>
        <p><b>Route:</b> {run.route} · <b>Specialist:</b> {run.kind} · <b>Status:</b> {run.status} · ${Number(run.cost_usd).toFixed(4)}</p>
        <p><b>Answer:</b> {run.answer}</p>
      </div>
      <div className="panel" data-testid="trace">
        <b>Trace ({run.trace.length} steps)</b>
        {run.trace.length === 0 && <p className="muted">No agent steps: answered from documents.</p>}
        {run.trace.map((s) => (
          <details key={s.index} open={s.kind === "tool"}>
            <summary>{s.index}. {s.kind === "tool" ? `tool: ${s.name}` : "model"} <span className="muted">{s.latency_ms} ms</span></summary>
            <pre>{JSON.stringify(s.detail, null, 1)}</pre>
          </details>
        ))}
      </div>
    </>
  );
}
