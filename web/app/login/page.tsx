"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { post } from "@/lib/api";

export default function Login() {
  const router = useRouter();
  const [username, setUsername] = useState("asha");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await post("/v1/auth/login", { username, password });
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    }
  }

  return (
    <div className="panel" style={{ maxWidth: 420, margin: "60px auto" }}>
      <h1>Sign in</h1>
      <p className="muted">
        Demo users (synthetic): <b>asha</b> and <b>ravi</b> (Corvane Freight on-call; ravi is the on-call lead),{" "}
        <b>j.patel</b> (Tallowmere review nurse).
      </p>
      <form onSubmit={submit} className="row" style={{ flexDirection: "column", alignItems: "stretch" }}>
        <label>Username <input name="username" value={username} onChange={(e) => setUsername(e.target.value)} /></label>
        <label>Password <input name="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <button type="submit">Sign in</button>
        {error && <p role="alert">{error}</p>}
      </form>
    </div>
  );
}
