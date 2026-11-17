"use client";

import { useEffect, useState } from "react";
import { get } from "@/lib/api";

type Metrics = Record<string, unknown>;

export default function Evals() {
  const [m, setM] = useState<Metrics | null>(null);
  useEffect(() => { get<Metrics>("/v1/evals/latest").then(setM).catch(() => setM({})); }, []);
  if (!m) return <p className="muted">Loading...</p>;
  const sections = Object.entries(m).filter(([, v]) => typeof v === "object" && v !== null) as [string, Record<string, unknown>][];
  return (
    <>
      <h1>Evaluation suite</h1>
      <p className="muted">
        Latest CI run: {String(m.mode ?? "?")} model, {String(m.embedder ?? "?")} embeddings, {String(m.at ?? "")}.
        Fake-model numbers measure the plumbing; real-model runs replace them when a key is configured.
      </p>
      <div className="panel" data-testid="evals">
        <table>
          <thead><tr><th>Area</th><th>Metrics</th></tr></thead>
          <tbody>
            {sections.map(([name, values]) => (
              <tr key={name}><td><b>{name}</b></td>
                <td>{Object.entries(values).map(([k, v]) => <span key={k} className="chip" style={{ marginRight: 6 }}>{k}: {Array.isArray(v) ? v.join(", ") : String(v)}</span>)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
