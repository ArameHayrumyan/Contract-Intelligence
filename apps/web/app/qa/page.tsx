"use client";

import { useState } from "react";

import { ApiError, askQuestion } from "@/lib/api-client";
import type { QAResponse } from "@/lib/types";

/** Cross-document QA chat over the tenant's contract set. */
export default function QAPage() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<QAResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    if (question.trim().length < 3) return;
    setBusy(true);
    setError(null);
    setAnswer(null);
    try {
      const res = await askQuestion({ question });
      setAnswer(res);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to answer question.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1>Ask your contracts</h1>
      <p className="muted">
        Answers are drawn only from your tenant&apos;s documents, with citations.
      </p>

      <form className="panel" onSubmit={ask}>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Which contracts auto-renew and what notice is required?"
          aria-label="Question"
        />
        <button
          className="btn"
          type="submit"
          disabled={busy || question.trim().length < 3}
          style={{ marginTop: 12 }}
        >
          {busy ? "Thinking…" : "Ask"}
        </button>
        {error ? <p className="error">{error}</p> : null}
      </form>

      {answer ? (
        <>
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Answer</h3>
            <p style={{ whiteSpace: "pre-wrap" }}>{answer.answer}</p>
          </div>
          {answer.citations.length > 0 ? (
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>Citations</h3>
              {answer.citations.map((c) => (
                <div className="clause" key={c.chunk_id}>
                  {c.snippet}
                  <div className="prov">
                    Document {c.document_id.slice(0, 8)}… · chunk{" "}
                    {c.chunk_id.slice(0, 8)}…
                    {c.page_number != null ? ` · page ${c.page_number}` : ""}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
