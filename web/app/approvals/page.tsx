"use client";

import { useCallback, useEffect, useState } from "react";
import { get, post, type Approval } from "@/lib/api";

export default function Approvals() {
  const [items, setItems] = useState<Approval[]>([]);
  const [reason, setReason] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  const load = useCallback(() => get<Approval[]>("/v1/approvals").then(setItems).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);

  async function decide(a: Approval, approve: boolean) {
    setError("");
    try {
      await post(`/v1/approvals/${a.id}/${approve ? "approve" : "reject"}`, { hash: a.hash, reason: reason[a.id] ?? "" });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    }
  }

  const pending = items.filter((a) => a.status === "pending");
  const decided = items.filter((a) => a.status !== "pending");
  return (
    <>
      <h1>Approvals</h1>
      <p className="muted">Nothing the copilot proposes runs until a person with the approver role approves exactly what is shown here.</p>
      {error && <p role="alert">{error}</p>}
      {pending.length === 0 && <p className="panel muted">No pending approvals.</p>}
      {pending.map((a) => (
        <div className="panel" key={a.id} data-testid={`approval-${a.id}`}>
          <div className="row"><b>{a.id}</b><span className="chip">{a.tool}</span>
            <span className="muted">expires {new Date(a.expires_at).toLocaleTimeString()}</span></div>
          <pre>{a.summary}</pre>
          <input placeholder="Reason (recorded in the audit log)" value={reason[a.id] ?? ""}
            onChange={(e) => setReason({ ...reason, [a.id]: e.target.value })} />
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={() => decide(a, true)}>Approve</button>
            <button className="secondary" onClick={() => decide(a, false)}>Reject</button>
          </div>
        </div>
      ))}
      {decided.length > 0 && (
        <div className="panel">
          <b>Decided</b>
          <table><tbody>
            {decided.map((a) => (
              <tr key={a.id}><td>{a.id}</td><td>{a.tool}</td><td><span className="chip">{a.status}</span></td>
                <td>{a.decided_by ?? ""}</td><td className="muted">{a.reason}</td></tr>
            ))}
          </tbody></table>
        </div>
      )}
    </>
  );
}
