"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { cleanVendor } from "@/components/ContractPanel";
import { Button } from "@/components/ui/Button";
import { askQuestion, listContracts } from "@/lib/api-client";
import type { ContractRow, QACitation } from "@/lib/types";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  citations?: QACitation[];
  ts: number;
}
interface Session {
  id: number;
  messages: ChatMessage[];
}

const SUGGESTIONS = [
  "Which contracts auto-renew and what notice is required?",
  "Are there any liability caps below industry standard?",
  "Which contracts expire in the next 90 days?",
  "Summarize the highest-risk contract in my portfolio.",
];

const STORAGE_KEY = "qa-sessions-v1";

export default function QAPage() {
  const [sessions, setSessions] = useState<Session[]>([{ id: 1, messages: [] }]);
  const [activeId, setActiveId] = useState(1);
  const [hydrated, setHydrated] = useState(false);
  const [contracts, setContracts] = useState<ContractRow[]>([]);
  const [contextIds, setContextIds] = useState<Set<string>>(new Set());
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listContracts({ page: 1, page_size: 1000 }).then((p) => setContracts(p.items)).catch(() => {});
  }, []);

  // Hydrate saved conversations + any ?doc= context from the URL (client only).
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as { sessions: Session[]; activeId: number };
        if (parsed.sessions?.length) {
          setSessions(parsed.sessions);
          setActiveId(parsed.activeId ?? parsed.sessions[0]?.id ?? 1);
        }
      }
    } catch {
      /* ignore corrupt storage */
    }
    const doc = new URLSearchParams(window.location.search).get("doc");
    if (doc) setContextIds(new Set([doc]));
    setHydrated(true);
  }, []);

  // Persist conversations whenever they change (after hydration).
  useEffect(() => {
    if (!hydrated) return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessions, activeId }));
  }, [sessions, activeId, hydrated]);

  const active = sessions.find((s) => s.id === activeId) ?? sessions[0];

  const vendorById = useMemo(() => {
    const map: Record<string, string> = {};
    contracts.forEach((c) => (map[c.document_id] = cleanVendor(c.vendor_name) ?? "Contract"));
    return map;
  }, [contracts]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [active?.messages.length, loading]);

  function updateActive(fn: (s: Session) => Session) {
    setSessions((prev) => prev.map((s) => (s.id === activeId ? fn(s) : s)));
  }

  function newConversation() {
    const id = Math.max(0, ...sessions.map((s) => s.id)) + 1;
    setSessions((prev) => [...prev, { id, messages: [] }]);
    setActiveId(id);
    setSidebarOpen(false);
  }

  function selectSession(id: number) {
    setActiveId(id);
    setSidebarOpen(false);
  }

  async function send(question: string) {
    const q = question.trim();
    if (!q || loading) return;
    setInput("");
    updateActive((s) => ({
      ...s,
      messages: [...s.messages, { role: "user", content: q, ts: Date.now() }],
    }));
    setLoading(true);
    try {
      const ids = contextIds.size > 0 ? [...contextIds] : null;
      const res = await askQuestion({ question: q, document_ids: ids });
      updateActive((s) => ({
        ...s,
        messages: [
          ...s.messages,
          { role: "assistant", content: res.answer, citations: res.citations, ts: Date.now() },
        ],
      }));
    } catch (err) {
      updateActive((s) => ({
        ...s,
        messages: [
          ...s.messages,
          {
            role: "assistant",
            content:
              err instanceof Error ? `Error: ${err.message}` : "Something went wrong.",
            ts: Date.now(),
          },
        ],
      }));
    } finally {
      setLoading(false);
    }
  }

  function toggleContext(id: string) {
    setContextIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const contextLabel =
    contextIds.size === 0 ? "all contracts" : `${contextIds.size} contracts`;
  const messages = active?.messages ?? [];

  return (
    <div className="chat-layout">
      {sidebarOpen ? (
        <div className="chat-backdrop" onClick={() => setSidebarOpen(false)} />
      ) : null}
      <aside className={`chat-sidebar${sidebarOpen ? " is-open" : ""}`}>
        <Button fullWidth onClick={newConversation}>+ New Conversation</Button>
        <div className="stack" style={{ gap: "var(--space-1)" }}>
          {sessions
            .filter((s) => s.messages.length > 0 || s.id === activeId)
            .map((s) => {
              const first = s.messages.find((m) => m.role === "user");
              return (
                <button
                  key={s.id}
                  className={`chat-session${s.id === activeId ? " is-active" : ""}`}
                  onClick={() => selectSession(s.id)}
                >
                  {first ? first.content.slice(0, 60) : "New conversation"}
                </button>
              );
            })}
        </div>
        <div style={{ borderTop: "1px solid var(--color-border-default)", paddingTop: "var(--space-3)" }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong style={{ fontSize: "var(--text-sm)" }}>Context</strong>
            {contextIds.size > 0 ? (
              <span className="count-badge">Filtered ({contextIds.size})</span>
            ) : null}
          </div>
          <label className="row" style={{ marginTop: "var(--space-2)", fontSize: "var(--text-sm)" }}>
            <input
              type="checkbox"
              className="checkbox"
              checked={contextIds.size === 0}
              onChange={() => setContextIds(new Set())}
            />
            All contracts
          </label>
          <div className="stack" style={{ gap: "var(--space-1)", marginTop: "var(--space-2)" }}>
            {contracts.map((c) => (
              <label key={c.document_id} className="row" style={{ fontSize: "var(--text-sm)" }}>
                <input
                  type="checkbox"
                  className="checkbox"
                  checked={contextIds.has(c.document_id)}
                  onChange={() => toggleContext(c.document_id)}
                />
                {cleanVendor(c.vendor_name) ?? "Unnamed"}
              </label>
            ))}
          </div>
        </div>
      </aside>

      <div className="chat-main">
        <div className="chat-topbar">
          <span className="row">
            <button
              className="chat-toggle"
              onClick={() => setSidebarOpen((v) => !v)}
              aria-label="Toggle conversations"
              title="Conversations"
            >
              ☰
            </button>
            <strong>Ask your contracts</strong>
          </span>
          <span className="muted" style={{ fontSize: "var(--text-sm)" }}>
            Searching: {contextLabel}
          </span>
        </div>

        <div className="chat-messages">
          {messages.length === 0 ? (
            <div>
              <p className="muted" style={{ fontSize: "var(--text-sm)" }}>Try asking…</p>
              <div className="suggestions">
                {SUGGESTIONS.map((q) => (
                  <button key={q} className="suggestion-card" onClick={() => send(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, i) => (
              <div key={i} className={`chat-msg${m.role === "user" ? " chat-msg--user" : ""}`}>
                <div className={m.role === "user" ? "chat-bubble-user" : "chat-bubble-assistant"}>
                  <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
                  {m.citations && m.citations.length > 0 ? (
                    <div className="chat-citations">
                      <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>
                        Sources:
                      </span>
                      {m.citations.map((c, ci) => (
                        <Link
                          key={ci}
                          href={`/audit/${c.document_id}?tab=clauses`}
                          className="citation-chip"
                        >
                          {(vendorById[c.document_id] ?? "Contract")} · p.{c.page_number ?? "?"}
                        </Link>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
            ))
          )}
          {loading ? (
            <div className="chat-msg">
              <div className="chat-bubble-assistant">
                <span className="typing-dot" />
                <span className="typing-dot" />
                <span className="typing-dot" />
              </div>
            </div>
          ) : null}
          <div ref={endRef} />
        </div>

        <div className="chat-input">
          <textarea
            className="textarea"
            style={{ minHeight: 44 }}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            placeholder="Ask anything about your contracts..."
            maxLength={2000}
            disabled={loading}
          />
          <Button onClick={() => send(input)} disabled={loading || !input.trim()}>
            Ask
          </Button>
        </div>
        {input.length > 1500 ? (
          <div className="text-muted" style={{ textAlign: "right", padding: "0 var(--space-6) var(--space-2)" }}>
            {input.length} / 2000
          </div>
        ) : null}
      </div>
    </div>
  );
}
