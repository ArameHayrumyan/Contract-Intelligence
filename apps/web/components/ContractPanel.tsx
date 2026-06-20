"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { RiskBadge } from "@/components/RiskBadge";
import { downloadPdf } from "@/lib/api-client";
import type { ContractRow } from "@/lib/types";

interface ContractPanelProps {
  /** The contract to show, or null when the drawer is closed. */
  contract: ContractRow | null;
  /** Called when the drawer should close. */
  onClose: () => void;
}

/** Format an ISO date as e.g. "15 Jan 2026", or a dash when absent. */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

/** Right-side drawer summarising one contract (reused by dashboard + monitoring). */
export function ContractPanel({ contract, onClose }: ContractPanelProps) {
  const [showAllClauses, setShowAllClauses] = useState(false);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    if (contract) {
      window.addEventListener("keydown", onKey);
      return () => window.removeEventListener("keydown", onKey);
    }
    return undefined;
  }, [contract, onClose]);

  // Reset expansion when a different contract is opened.
  useEffect(() => {
    setShowAllClauses(false);
  }, [contract?.document_id]);

  if (!contract) return null;

  const clauses = showAllClauses
    ? contract.critical_clauses
    : contract.critical_clauses.slice(0, 3);

  async function onExport() {
    if (!contract) return;
    setExporting(true);
    try {
      await downloadPdf(
        `/api/documents/${encodeURIComponent(contract.document_id)}/export/pdf`,
        `audit_${contract.vendor_name}.pdf`,
      );
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside
        className="drawer"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`${contract.vendor_name} summary`}
      >
        <header className="drawer-head">
          <div>
            <h2 style={{ margin: 0 }}>{contract.vendor_name}</h2>
            <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
              <RiskBadge score={contract.risk_score} />
              <span className={`pill status--${contract.status}`}>
                {contract.status}
              </span>
            </div>
          </div>
          <button className="link-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <section className="drawer-section">
          <dl className="kv">
            <dt>Contract type</dt>
            <dd>{contract.contract_type}</dd>
            <dt>Auto-renewal</dt>
            <dd>{contract.auto_renewal ? "Yes" : "No"}</dd>
            <dt>Notice period</dt>
            <dd>{contract.notice_period_days ?? "—"} days</dd>
            <dt>End date</dt>
            <dd>{formatDate(contract.contract_end_date)}</dd>
          </dl>
        </section>

        <section className="drawer-section">
          <h3>Risk rationale</h3>
          <p className="scroll-text">{contract.risk_rationale}</p>
        </section>

        <section className="drawer-section">
          <h3>Critical clauses</h3>
          {contract.critical_clauses.length === 0 ? (
            <p className="muted">None identified.</p>
          ) : (
            <>
              {clauses.map((c, i) => (
                <div key={`${c.source_chunk_id}-${i}`} className="clause">
                  <span className="prov">page {c.page_number ?? "?"}</span>
                  <p>
                    {c.text.length > 120 && !showAllClauses
                      ? `${c.text.slice(0, 120)}…`
                      : c.text}
                  </p>
                </div>
              ))}
              {contract.critical_clauses.length > 3 ? (
                <button
                  className="link-btn"
                  onClick={() => setShowAllClauses((v) => !v)}
                >
                  {showAllClauses ? "Show fewer" : "Show all"}
                </button>
              ) : null}
            </>
          )}
        </section>

        <section className="drawer-section">
          <h3>Cross-reference</h3>
          {contract.has_crossref ? (
            <Link href={`/crossref/${contract.document_id}`}>
              View cross-reference →
            </Link>
          ) : (
            <p className="muted">No cross-reference run yet</p>
          )}
        </section>

        <footer className="drawer-foot">
          <Link className="btn" href={`/audit/${contract.document_id}`}>
            View full audit
          </Link>
          <button className="btn" onClick={onExport} disabled={exporting}>
            {exporting ? "Exporting…" : "Export PDF"}
          </button>
        </footer>
      </aside>
    </div>
  );
}
