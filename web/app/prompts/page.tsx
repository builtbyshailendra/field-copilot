"use client";

import { useEffect, useState } from "react";
import { get } from "@/lib/api";

type PromptRow = { id: string; version: string; source: string; description: string };

export default function Prompts() {
  const [rows, setRows] = useState<PromptRow[]>([]);
  useEffect(() => { get<PromptRow[]>("/v1/admin/prompts").then(setRows).catch(() => setRows([])); }, []);
  return (
    <div className="panel">
      <h1>Prompts in use</h1>
      <p className="muted">Each version is a hash of the prompt&apos;s content. Recorded responses and evaluation results name the version that produced them.</p>
      <table>
        <thead><tr><th>Prompt</th><th>Version</th><th>Source</th><th>What it does</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.id + r.source}><td>{r.id}</td><td><code>{r.version}</code></td><td>{r.source}</td><td>{r.description}</td></tr>)}</tbody>
      </table>
    </div>
  );
}
