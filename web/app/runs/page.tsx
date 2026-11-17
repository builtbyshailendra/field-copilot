"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { get } from "@/lib/api";

type RunRow = { id: string; username: string; kind: string; question: string; route: string; status: string; cost_usd: number; created_at: string };

export default function Runs() {
  const [rows, setRows] = useState<RunRow[]>([]);
  useEffect(() => { get<RunRow[]>("/v1/runs").then(setRows).catch(() => setRows([])); }, []);
  return (
    <div className="panel">
      <h1>Recent runs</h1>
      <table>
        <thead><tr><th>When</th><th>Who</th><th>Question</th><th>Specialist</th><th>Status</th><th>Cost</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="muted">{new Date(r.created_at).toLocaleString()}</td><td>{r.username}</td>
              <td><Link href={`/runs/${r.id}`}>{r.question}</Link></td><td>{r.kind}</td>
              <td><span className="chip">{r.status}</span></td><td>${r.cost_usd.toFixed(4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
