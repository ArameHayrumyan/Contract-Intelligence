"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { RiskBadge } from "@/components/RiskBadge";
import { ApiError, getAudit } from "@/lib/api-client";
import type { ContractAudit } from "@/lib/types";

/** Per-document audit view with provenance-bearing critical clauses. */
export default function AuditPage() {
  const params = useParams<{ documentId: string }>();
  const documentId = params.documentId;
  const [audit, setAudit] = useState<ContractAudit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const result = await getAudit(documentId);
        if (!cancelled) setAudit(result);
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 409) {
            setError("Document is still being ingested. Try again shortly.");
          } else {
            setError(err instanceof Error ? err.message : "Failed to load audit.");
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  if (loading) {
    return <p className="muted">Generating audit…</p>;
  }
  if (error) {
    return <p className="error">{error}</p>;
  }
  if (!audit) {
    return <p className="muted">No audit available.</p>;
  }

  return (
    <div>
      <div
        style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}
      >
        <h1 style={{ margin: 0 }}>{audit.vendor_name}</h1>
        <RiskBadge score={audit.risk_score} />
        <Link
          className="btn"
          href={`/crossref/${documentId}`}
          style={{ marginLeft: "auto" }}
        >
          Cross-reference →
        </Link>
      </div>

      <div className="panel">
        <dl className="kv">
          <dt>Contract type</dt>
          <dd>{audit.contract_type}</dd>
          <dt>Auto-renewal</dt>
          <dd>{audit.auto_renewal ? "Yes" : "No"}</dd>
          <dt>Notice period</dt>
          <dd>{audit.notice_period_days} days</dd>
          <dt>Liability cap</dt>
          <dd>{audit.liability_cap_description}</dd>
        </dl>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Risk rationale</h3>
        <p>{audit.risk_rationale}</p>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>
          Critical clauses ({audit.critical_clauses.length})
        </h3>
        {audit.critical_clauses.length === 0 ? (
          <p className="muted">No critical clauses identified.</p>
        ) : (
          audit.critical_clauses.map((clause) => (
            <div className="clause" key={clause.source_chunk_id + clause.text.slice(0, 16)}>
              {clause.category ? (
                <strong>{clause.category}: </strong>
              ) : null}
              {clause.text}
              <div className="prov">
                Source: chunk {clause.source_chunk_id.slice(0, 8)}…
                {clause.page_number != null ? ` · page ${clause.page_number}` : ""}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
