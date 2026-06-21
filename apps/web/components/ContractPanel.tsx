"use client";

import Link from "next/link";
import { useState } from "react";

import { AnnotationPanel } from "@/components/AnnotationPanel";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { useToast } from "@/components/ui/Toast";
import { downloadPdf } from "@/lib/api-client";
import type { ContractRow } from "@/lib/types";

const VENDOR_PLACEHOLDERS = new Set([
  "",
  "not specified",
  "unspecified",
  "n/a",
  "na",
  "none",
  "unknown",
  "not stated",
  "not available",
]);

/**
 * Normalise a vendor name: the LLM sometimes returns "Not specified" / "N/A"
 * etc., which read as errors. Return the trimmed name, or null for a fallback.
 */
export function cleanVendor(name: string | null | undefined): string | null {
  if (!name) return null;
  const trimmed = name.trim();
  if (!trimmed || VENDOR_PLACEHOLDERS.has(trimmed.toLowerCase())) return null;
  return trimmed;
}

/** Format an ISO date as "15 Jan 2026"; "—" when absent. */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

/** Days until an ISO date, or null. */
export function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return Math.ceil((d.getTime() - Date.now()) / 86400000);
}

/** CSS class for an end-date cell based on urgency. */
export function endDateClass(iso: string | null): string {
  const days = daysUntil(iso);
  if (days === null || days < 0) return "";
  if (days <= 30) return "cell-date-high";
  if (days <= 60) return "cell-date-medium";
  return "";
}

interface ContractPanelProps {
  contract: ContractRow | null;
  onClose: () => void;
}

/** Right-side detail drawer for a contract row. */
export function ContractPanel({ contract, onClose }: ContractPanelProps) {
  const { toast } = useToast();
  const [showAllClauses, setShowAllClauses] = useState(false);
  const [exporting, setExporting] = useState(false);

  if (!contract) return null;
  const vendor = cleanVendor(contract.vendor_name) ?? "Unnamed Contract";
  const clauses = showAllClauses
    ? contract.critical_clauses
    : contract.critical_clauses.slice(0, 3);

  async function copyId() {
    if (!contract) return;
    try {
      await navigator.clipboard.writeText(contract.document_id);
      toast("Document ID copied.", "success");
    } catch {
      toast("Copy failed.", "error");
    }
  }

  async function onExport() {
    if (!contract) return;
    setExporting(true);
    try {
      await downloadPdf(
        `/api/documents/${encodeURIComponent(contract.document_id)}/export/pdf`,
        `audit_${vendor}.pdf`,
      );
    } finally {
      setExporting(false);
    }
  }

  return (
    <Drawer isOpen={contract !== null} onClose={onClose}>
      <div className="drawer__header">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ fontSize: "var(--text-xl)", fontWeight: 600 }}>{vendor}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="row" style={{ marginTop: "var(--space-2)" }}>
          <Badge kind="risk" score={contract.risk_score} />
          <Badge kind="status" status={contract.status} />
        </div>
      </div>

      <div className="drawer__body">
        <section>
          <div className="panel-section__label">Contract Details</div>
          <dl className="kv-grid">
            <dt>Contract Type</dt>
            <dd>
              {cleanVendor(contract.contract_type) ?? (
                <span className="cell-empty">—</span>
              )}
            </dd>
            <dt>Auto-Renewal</dt>
            <dd>{contract.auto_renewal ? "Yes" : "No"}</dd>
            <dt>Notice Period</dt>
            <dd>{contract.notice_period_days ?? "—"} days</dd>
            <dt>End Date</dt>
            <dd className={endDateClass(contract.contract_end_date)}>
              {formatDate(contract.contract_end_date)}
            </dd>
            <dt>Document ID</dt>
            <dd>
              <button className="copy-btn" onClick={copyId} title="Copy">
                {contract.document_id.slice(0, 12)}… ⧉
              </button>
            </dd>
          </dl>
        </section>

        <section className="panel-section">
          <div className="panel-section__label">Risk Rationale</div>
          <p style={{ lineHeight: 1.6, fontSize: "var(--text-sm)" }}>
            {contract.risk_rationale}
          </p>
        </section>

        <section className="panel-section">
          <div className="panel-section__label">
            Critical Clauses ({contract.critical_clauses.length})
          </div>
          {contract.critical_clauses.length === 0 ? (
            <p className="muted">None identified.</p>
          ) : (
            <>
              {clauses.map((c, i) => (
                <div key={`${c.source_chunk_id}-${i}`} className="clause-card">
                  <p className={showAllClauses ? "" : "line-clamp-3"}>{c.text}</p>
                  <div className="text-muted" style={{ fontSize: "var(--text-xs)" }}>
                    Page {c.page_number ?? "?"}
                  </div>
                </div>
              ))}
              {contract.critical_clauses.length > 3 ? (
                <button
                  className="link-btn"
                  onClick={() => setShowAllClauses((v) => !v)}
                >
                  {showAllClauses
                    ? "Show fewer"
                    : `Show all ${contract.critical_clauses.length} clauses`}
                </button>
              ) : null}
            </>
          )}
        </section>

        <section className="panel-section">
          <div className="panel-section__label">Cross-Reference</div>
          {contract.has_crossref ? (
            <span style={{ color: "var(--color-risk-low)" }}>
              Cross-reference available ·{" "}
              <Link href={`/crossref/${contract.document_id}`}>View Report →</Link>
            </span>
          ) : (
            <span className="muted">
              No cross-reference run ·{" "}
              <Link href={`/crossref/${contract.document_id}`}>
                Run cross-reference →
              </Link>
            </span>
          )}
        </section>

        <section className="panel-section">
          <div className="panel-section__label">Reviewer Notes</div>
          <AnnotationPanel
            documentId={contract.document_id}
            targetType="document"
            compact
          />
        </section>
      </div>

      <div className="drawer__footer">
        <Link
          className="btn btn--primary btn--md btn--full"
          href={`/audit/${contract.document_id}`}
        >
          View Full Audit
        </Link>
        <Button variant="secondary" fullWidth onClick={onExport} loading={exporting}>
          Export PDF
        </Button>
      </div>
    </Drawer>
  );
}
