"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

function AccessForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/access", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body?.detail ?? "Incorrect access code.");
        return;
      }
      const next = params.get("next") || "/upload";
      router.replace(next);
      router.refresh();
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" style={{ width: 360 }} onSubmit={submit}>
      <h2 style={{ marginTop: 0 }}>⚖️ Contract Intelligence</h2>
      <p className="muted">Enter your access code to continue.</p>
      <input
        type="password"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        placeholder="Access code"
        autoFocus
        aria-label="Access code"
      />
      {error ? (
        <p className="error" style={{ marginBottom: 0 }}>
          {error}
        </p>
      ) : null}
      <button
        className="btn btn--primary btn--md"
        type="submit"
        disabled={busy || code.length === 0}
        style={{ marginTop: 14, width: "100%" }}
      >
        {busy ? "Checking…" : "Enter"}
      </button>
    </form>
  );
}

export default function AccessPage() {
  return (
    <div className="center">
      <Suspense fallback={<div className="panel" style={{ width: 360 }}>Loading…</div>}>
        <AccessForm />
      </Suspense>
    </div>
  );
}
