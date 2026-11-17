"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { get, post, type Me } from "@/lib/api";

export function Nav() {
  const [me, setMe] = useState<Me | null>(null);
  const path = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (path === "/login") return;
    get<Me>("/v1/me").then(setMe).catch(() => router.push("/login"));
  }, [path, router]);

  if (path === "/login") return null;
  return (
    <header className="top">
      <strong>field-copilot</strong>
      <nav>
        <Link href="/">Ask</Link>
        <Link href="/approvals">Approvals</Link>
        <Link href="/runs">Runs</Link>
        <Link href="/evals">Evaluations</Link>
        <Link href="/prompts">Prompts</Link>
      </nav>
      {me && (
        <span className="who">
          {me.user} · {me.tenant_name} · {me.roles.join(", ")}{" "}
          <button className="secondary" onClick={async () => { await post("/v1/auth/logout"); router.push("/login"); }}>
            Sign out
          </button>
        </span>
      )}
    </header>
  );
}
